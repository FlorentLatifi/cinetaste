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
    # httpx logs every request URL at INFO. TMDb v3 puts the API key in the
    # query string, so those lines would write the key into the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # Quiet noisy default loggers in production-like runs
    if not debug:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
