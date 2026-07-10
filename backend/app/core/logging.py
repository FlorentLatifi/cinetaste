import logging
import sys


def configure_logging(*, debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    # Single-line structured-ish logs (friendly to Render/Railway log drains)
    logging.basicConfig(
        level=level,
        format="%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s",
        stream=sys.stdout,
        force=True,
    )
    # Quiet noisy default loggers in production-like runs
    if not debug:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
