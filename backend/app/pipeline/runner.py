"""Resumable stage runner (SRS 3.3).

The persisted business status is the only progress marker, so a restarted worker
simply claims the business again and continues from the last committed stage.
Each stage is its own transaction: the handler's writes, the status change and the
audit row commit together or not at all. A worker whose lease lapsed cannot commit
(LeaseLost), and a stage that another worker already completed is skipped
(StaleState), so completed stages never run twice.
"""

import contextvars
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.config import WORKER_CLAIM_TTL_SECONDS
from app.models import Business, BusinessStatus
from app.pipeline import repository, status
from app.pipeline.status import RETRYABLE_STAGES, Actor

logger = logging.getLogger(__name__)

# Filled per claim so every log line from a run can carry business_id/run_id.
current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")


@dataclass(frozen=True)
class StageResult:
    next_status: BusinessStatus
    reason: str
    version_ref: str | None = None


# A handler does the work for the stage the business is *currently in* and says where to go.
Handler = Callable[[Session, Business], StageResult]


def run_business(
    session_factory: sessionmaker[Session],
    business_id: uuid.UUID,
    *,
    worker_id: str,
    stages: dict[BusinessStatus, Handler],
    should_stop: Callable[[], bool] = lambda: False,
    ttl_seconds: int = WORKER_CLAIM_TTL_SECONDS,
) -> BusinessStatus | None:
    """Advance a claimed business until it is terminal, FAILED, stopped or the lease is lost.

    Returns the last status observed, or None if the business vanished.
    """
    run_id = uuid.uuid4().hex[:12]
    token = current_run_id.set(run_id)
    actor = Actor.worker(worker_id)
    prefix = f"business_id={business_id}"
    last: BusinessStatus | None = None
    try:
        while True:
            with session_factory() as session:
                business = repository.get_business(session, business_id)
                if business is None:
                    return None
                last = current = BusinessStatus(business.status)
                if current not in RETRYABLE_STAGES:  # DONE, REJECTED or FAILED
                    logger.info("%s stopping at %s", prefix, current)
                    return last
                if should_stop():
                    logger.info("%s stop requested; will resume from %s", prefix, current)
                    return last
                if not repository.renew_lease(session, business_id, worker_id, ttl_seconds):
                    logger.warning("%s lease lost before %s; abandoning", prefix, current)
                    return last

                try:
                    result = stages[current](session, business)
                    status.transition(
                        session,
                        business_id,
                        result.next_status,
                        actor=actor,
                        action=f"stage.{current.value.lower()}",
                        reason=result.reason,
                        version_ref=result.version_ref,
                        expected_from=current,
                        worker_id=worker_id,
                    )
                    session.commit()
                    logger.info("%s stage %s -> %s", prefix, current, result.next_status)
                except (status.StaleState, status.LeaseLost) as exc:
                    session.rollback()
                    logger.warning("%s skipping %s: %s", prefix, current, exc)
                    return last
                except Exception as exc:
                    session.rollback()
                    logger.exception("%s stage %s raised", prefix, current)
                    last = _record_failure(session, business_id, current, actor, worker_id, exc)
                    return last
    finally:
        repository.release_lease_quietly(session_factory, business_id, worker_id)
        current_run_id.reset(token)


def _record_failure(
    session: Session,
    business_id: uuid.UUID,
    stage: BusinessStatus,
    actor: Actor,
    worker_id: str,
    exc: Exception,
) -> BusinessStatus:
    message = f"{type(exc).__name__}: {exc}"
    try:
        status.fail(
            session,
            business_id,
            actor=actor,
            error=message,
            expected_from=stage,
            worker_id=worker_id,
        )
        session.commit()
        return BusinessStatus.FAILED
    except (status.StaleState, status.LeaseLost) as inner:
        session.rollback()
        logger.warning(
            "business_id=%s could not record failure of %s: %s", business_id, stage, inner
        )
        return stage
