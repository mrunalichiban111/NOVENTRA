import logging
import os
import signal
import socket
import threading
import uuid

from app.config import WORKER_CLAIM_TTL_SECONDS, WORKER_POLL_SECONDS
from app.database import SessionLocal
from app.models import BusinessStatus
from app.pipeline import arena, repository
from app.pipeline.handlers import pipeline_stages
from app.pipeline.runner import Handler, current_run_id, run_business
from app.pipeline.status import RETRYABLE_STAGES

logger = logging.getLogger(__name__)
stop_event = threading.Event()


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = current_run_id.get()
        return True


def make_worker_id() -> str:
    return f"{socket.gethostname()[:30]}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def stop_worker(signum: int, _frame: object) -> None:
    logger.info("Worker received signal %s; finishing current stage then stopping", signum)
    stop_event.set()


def work_once(worker_id: str, stages: dict[BusinessStatus, Handler] | None = None) -> bool:
    """Claim and run one business. Returns False if nothing was claimable."""
    stages = stages or pipeline_stages()
    halted = RETRYABLE_STAGES - stages.keys()  # stop points (e.g. GATED) are never claimed
    with SessionLocal() as session:
        business = repository.claim_next(
            session, worker_id, WORKER_CLAIM_TTL_SECONDS, exclude=halted
        )
        business_id = business.id if business else None
    if business_id is None:
        return False
    run_business(
        SessionLocal,
        business_id,
        worker_id=worker_id,
        stages=stages,
        should_stop=stop_event.is_set,
    )
    return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s run_id=%(run_id)s %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_RunIdFilter())
    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    worker_id = make_worker_id()
    logger.info("NOVENTRA worker %s started", worker_id)
    while not stop_event.is_set():
        try:
            # Pipeline stages first; persona arena jobs (M5) when no business is runnable.
            did_work = work_once(worker_id) or arena.work_once(SessionLocal, worker_id)
        except Exception:
            logger.exception("Worker loop error; backing off")
            did_work = False
        if not did_work:
            stop_event.wait(WORKER_POLL_SECONDS)
    logger.info("NOVENTRA worker stopped")


if __name__ == "__main__":
    main()
