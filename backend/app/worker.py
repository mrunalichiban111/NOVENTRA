import logging
import os
import signal
import socket
import threading
import uuid

from app.config import WORKER_CLAIM_TTL_SECONDS, WORKER_POLL_SECONDS
from app.database import SessionLocal
from app.models import BusinessStatus
from app.pipeline import repository
from app.pipeline.runner import Handler, current_run_id, run_business
from app.pipeline.stubs import default_stages

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
    with SessionLocal() as session:
        business = repository.claim_next(session, worker_id, WORKER_CLAIM_TTL_SECONDS)
        business_id = business.id if business else None
    if business_id is None:
        return False
    run_business(
        SessionLocal,
        business_id,
        worker_id=worker_id,
        stages=stages or default_stages(),
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
    logger.info("NOVENTRA worker %s started (stub pipeline)", worker_id)
    while not stop_event.is_set():
        try:
            did_work = work_once(worker_id)
        except Exception:
            logger.exception("Worker loop error; backing off")
            did_work = False
        if not did_work:
            stop_event.wait(WORKER_POLL_SECONDS)
    logger.info("NOVENTRA worker stopped")


if __name__ == "__main__":
    main()
