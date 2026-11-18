import logging
import signal
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
running = True


def stop_worker(signum: int, _frame: object) -> None:
    global running
    logger.info("Worker received signal %s; shutting down", signum)
    running = False


def main() -> None:
    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    logger.info("NOVENTRA worker started; waiting for future jobs")
    while running:
        time.sleep(1)
    logger.info("NOVENTRA worker stopped")


if __name__ == "__main__":
    main()
