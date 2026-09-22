import time

from prefect import flow, get_run_logger


@flow(name="listener", retries=3, retry_delay_seconds=10)
def listener(seconds: int = 60):
    """Bounded demo: use a cancellation-aware loop for your own listener."""
    logger = get_run_logger()
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        logger.info("Listener alive; other flows can run concurrently.")
        time.sleep(2)
    return "listener finished"
