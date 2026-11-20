"""Business status machine and audited transitions (SRS 3.3, FR-GT-2).

All status changes go through `transition`, `fail` or `retry`. Each one locks the
business row, validates against TRANSITIONS, updates the status and inserts the
audit entry in the caller's transaction. Callers own commit/rollback, so a status
change and its audit row can never be persisted separately.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import MAX_STAGE_ATTEMPTS
from app.models import AuditLog, Business, BusinessStatus

logger = logging.getLogger(__name__)

S = BusinessStatus

# The happy path in order. DONE and REJECTED are terminal.
PIPELINE_ORDER: tuple[BusinessStatus, ...] = (
    S.INTAKE, S.PREMORTEM, S.GATED, S.DOMAIN_PENDING, S.DEPLOYING,
    S.LIVE, S.TESTING, S.IMPROVING, S.DONE,
)  # fmt: skip

# Stages where the pipeline deliberately pauses (no automatic next stage yet). In M3 the
# pipeline stops at GATED for every verdict; a later milestone adds the approval/override.
# DOMAIN_PENDING (M4A) waits for the deploy stage (M4B). The API reports next_stage=None for
# these, and handlers must not exist for them. The worker derives its halted statuses from
# handler keys, and retry() uses failed_stage, so neither depends on this set.
# LIVE (M4B) waits for the persona arena (M5).
PAUSE_STAGES: frozenset[BusinessStatus] = frozenset({S.GATED, S.DOMAIN_PENDING, S.LIVE})

TERMINAL: frozenset[BusinessStatus] = frozenset({S.DONE, S.REJECTED})
RETRYABLE_STAGES: frozenset[BusinessStatus] = frozenset(
    s for s in PIPELINE_ORDER if s not in TERMINAL
)

TRANSITIONS: dict[BusinessStatus, frozenset[BusinessStatus]] = {
    S.INTAKE: frozenset({S.PREMORTEM, S.FAILED}),
    S.PREMORTEM: frozenset({S.GATED, S.REJECTED, S.FAILED}),
    S.GATED: frozenset({S.DOMAIN_PENDING, S.REJECTED, S.FAILED}),
    S.DOMAIN_PENDING: frozenset({S.DEPLOYING, S.REJECTED, S.FAILED}),
    S.DEPLOYING: frozenset({S.LIVE, S.FAILED}),
    S.LIVE: frozenset({S.TESTING, S.FAILED}),
    S.TESTING: frozenset({S.IMPROVING, S.DONE, S.FAILED}),
    S.IMPROVING: frozenset({S.DONE, S.FAILED}),
    S.DONE: frozenset(),
    S.REJECTED: frozenset(),
    # Retry: FAILED may only return to the stage it failed in (enforced in `retry`).
    S.FAILED: RETRYABLE_STAGES,
}


class StatusError(Exception):
    """Base class for status-machine errors."""


class BusinessNotFound(StatusError):
    def __init__(self, business_id: uuid.UUID) -> None:
        super().__init__(f"Business {business_id} not found")
        self.business_id = business_id


class InvalidTransition(StatusError):
    def __init__(self, current: str, target: str, detail: str = "") -> None:
        msg = f"Invalid transition {current} -> {target}"
        super().__init__(f"{msg}: {detail}" if detail else msg)
        self.current, self.target = current, target


class StaleState(StatusError):
    """The business is no longer in the status the caller expected (someone else moved it)."""


class LeaseLost(StatusError):
    """The caller's worker lease expired or was taken over; it must not write."""


class RetryNotAllowed(StatusError):
    """Retry requested for a business that is not FAILED."""


class RetryLimitExceeded(StatusError):
    def __init__(self, attempts: int, limit: int) -> None:
        super().__init__(f"Retry limit reached ({attempts}/{limit} failures)")
        self.attempts, self.limit = attempts, limit


@dataclass(frozen=True)
class Actor:
    """Who performed the action: a user, or an automated agent/worker."""

    name: str

    @classmethod
    def user(cls) -> "Actor":
        return cls("user")

    @classmethod
    def worker(cls, worker_id: str) -> "Actor":
        return cls(f"worker:{worker_id}")


def can_transition(current: str, target: str) -> bool:
    return BusinessStatus(target) in TRANSITIONS.get(BusinessStatus(current), frozenset())


def _lock(session: Session, business_id: uuid.UUID) -> Business:
    # populate_existing: pick up changes committed by others before we got the lock.
    business = session.execute(
        select(Business)
        .where(Business.id == business_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if business is None:
        raise BusinessNotFound(business_id)
    return business


def _check_lease(session: Session, business: Business, worker_id: str | None) -> None:
    if worker_id is None:
        return
    now = session.execute(select(func.now())).scalar_one()
    if (
        business.locked_by != worker_id
        or business.locked_until is None
        or business.locked_until <= now
    ):
        raise LeaseLost(f"Worker {worker_id} no longer holds the lease on {business.id}")


def _audit(
    session: Session,
    business: Business,
    *,
    actor: Actor,
    action: str,
    reason: str | None,
    version_ref: str | None,
    from_status: str | None,
    to_status: str,
    key: str,
    details: dict | None = None,
) -> bool:
    """Insert one audit row; returns False if this idempotency key was already recorded."""
    stmt = (
        pg_insert(AuditLog)
        .values(
            business_id=business.id,
            actor=actor.name,
            action=action,
            reason=reason,
            version_ref=version_ref,
            from_status=from_status,
            to_status=to_status,
            idempotency_key=key,
            details=details or {},
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(AuditLog.id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def assert_lease(session: Session, business: Business, worker_id: str) -> None:
    """Raise LeaseLost unless `worker_id` holds a live lease on this (already locked) business."""
    _check_lease(session, business, worker_id)


def lock_business(session: Session, business_id: uuid.UUID) -> Business:
    """Row-lock a business for the rest of the caller's transaction (public wrapper)."""
    return _lock(session, business_id)


def record_event(
    session: Session,
    business: Business,
    *,
    actor: Actor,
    action: str,
    reason: str | None,
    version_ref: str | None = None,
    details: dict | None = None,
    key: str | None = None,
) -> bool:
    """Audit a non-transition event (e.g. an assumption edit). Status is unchanged. No commit.

    `key` is an optional idempotency key; returns False if that key was already recorded.
    """
    return _audit(
        session,
        business,
        actor=actor,
        action=action,
        reason=reason,
        version_ref=version_ref,
        from_status=business.status,
        to_status=business.status,
        key=key or f"{business.id}:event:{uuid.uuid4().hex}",
        details=details,
    )


def record_creation(session: Session, business: Business, actor: Actor) -> None:
    """Initial audit entry for a freshly inserted business (status INTAKE)."""
    _audit(
        session,
        business,
        actor=actor,
        action="business.created",
        reason="Idea submitted",
        version_ref=None,
        from_status=None,
        to_status=BusinessStatus.INTAKE,
        key=f"{business.id}:created",
    )


def _key(business: Business, current: str, target: str, ref: str | None = None) -> str:
    # `attempts` (failure count) separates repeats of the same edge after a retry. `ref` is a
    # stable identity of the logical transition (e.g. "site:<site_version_id>") for edges a
    # business can legitimately take more than once without failing in between, such as
    # DEPLOYING -> LIVE for each new site version. Without a ref the key is unchanged (M1-M4A).
    base = f"{business.id}:{current}->{target}:{business.attempts}"
    return f"{base}:{ref}" if ref else base


def transition(
    session: Session,
    business_id: uuid.UUID,
    to_status: BusinessStatus,
    *,
    actor: Actor,
    action: str,
    reason: str | None = None,
    version_ref: str | None = None,
    expected_from: BusinessStatus | None = None,
    worker_id: str | None = None,
    key_ref: str | None = None,
) -> Business:
    """Move a business to `to_status` and write the audit row. Does not commit.

    key_ref: stable identity of this logical transition, added to the idempotency key so
    distinct lifecycles of the same edge do not collide while a duplicate execution of the
    same one is still refused (StaleState). Never random.

    expected_from: raise StaleState unless the business is currently in that status
    (guards against duplicate execution of an already-completed stage).
    worker_id: raise LeaseLost unless that worker still holds a live lease.
    """
    to_status = BusinessStatus(to_status)
    if to_status == S.FAILED:
        raise InvalidTransition("*", S.FAILED, "use fail()")
    business = _lock(session, business_id)
    current = BusinessStatus(business.status)
    if expected_from is not None and current != expected_from:
        raise StaleState(f"Business {business_id} is {current}, expected {expected_from}")
    _check_lease(session, business, worker_id)
    if current == S.FAILED:
        raise InvalidTransition(current, to_status, "use retry()")
    if to_status not in TRANSITIONS[current]:
        raise InvalidTransition(current, to_status)

    inserted = _audit(
        session,
        business,
        actor=actor,
        action=action,
        reason=reason,
        version_ref=version_ref,
        from_status=current,
        to_status=to_status,
        key=_key(business, current, to_status, key_ref),
    )
    if not inserted:
        raise StaleState(f"Transition {current} -> {to_status} already recorded for {business_id}")
    business.status = to_status
    session.flush()
    logger.info(
        "business_id=%s transition %s -> %s actor=%s", business.id, current, to_status, actor.name
    )
    return business


def fail(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor: Actor,
    error: str,
    expected_from: BusinessStatus | None = None,
    worker_id: str | None = None,
) -> Business:
    """Move a non-terminal business to FAILED, remembering the stage for retry. Does not commit."""
    business = _lock(session, business_id)
    current = BusinessStatus(business.status)
    if expected_from is not None and current != expected_from:
        raise StaleState(f"Business {business_id} is {current}, expected {expected_from}")
    _check_lease(session, business, worker_id)
    if current not in RETRYABLE_STAGES:
        raise InvalidTransition(current, S.FAILED)

    message = error[:2000]
    inserted = _audit(
        session,
        business,
        actor=actor,
        action="pipeline.failed",
        reason=message,
        version_ref=None,
        from_status=current,
        to_status=S.FAILED,
        key=_key(business, current, S.FAILED),
        details={"failed_stage": current.value, "attempt": business.attempts + 1},
    )
    if not inserted:
        raise StaleState(f"Failure of {current} already recorded for {business_id}")
    business.status = S.FAILED
    business.failed_stage = current
    business.last_error = message
    business.attempts += 1
    business.locked_by = None
    business.locked_until = None
    session.flush()
    logger.warning(
        "business_id=%s failed in %s (failure %s/%s): %s",
        business.id, current, business.attempts, MAX_STAGE_ATTEMPTS, message,
    )  # fmt: skip
    return business


def retry(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor: Actor,
    reason: str | None = None,
    max_attempts: int = MAX_STAGE_ATTEMPTS,
) -> Business:
    """FAILED -> the stage that failed, within the retry budget. Does not commit."""
    business = _lock(session, business_id)
    current = BusinessStatus(business.status)
    if current != S.FAILED or business.failed_stage is None:
        raise RetryNotAllowed(f"Business {business_id} is {current}; only FAILED can be retried")
    if business.attempts >= max_attempts:
        raise RetryLimitExceeded(business.attempts, max_attempts)

    target = BusinessStatus(business.failed_stage)
    inserted = _audit(
        session,
        business,
        actor=actor,
        action="pipeline.retry",
        reason=reason or f"Retrying {target.value}",
        version_ref=None,
        from_status=current,
        to_status=target,
        key=_key(business, current, target),
        details={"attempt": business.attempts, "max_attempts": max_attempts},
    )
    if not inserted:
        raise StaleState(f"Retry already recorded for {business_id}")
    business.status = target
    business.failed_stage = None
    business.last_error = None
    business.locked_by = None
    business.locked_until = None
    session.flush()
    logger.info("business_id=%s retry -> %s actor=%s", business.id, target, actor.name)
    return business
