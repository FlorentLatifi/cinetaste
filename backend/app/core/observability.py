"""Optional production observability (Sentry).

Sentry is **off** unless ``SENTRY_DSN`` is set — local/CI stay silent and free.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)

_sentry_enabled = False


def sentry_enabled() -> bool:
    return _sentry_enabled


def init_observability(settings: Settings) -> None:
    """Initialize Sentry for FastAPI if DSN is configured."""
    global _sentry_enabled
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        _sentry_enabled = False
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry_sdk not installed; SENTRY_DSN ignored")
        _sentry_enabled = False
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env,
        release=settings.sentry_release or None,
        traces_sample_rate=max(0.0, min(float(settings.sentry_traces_sample_rate), 1.0)),
        send_default_pii=False,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    _sentry_enabled = True
    logger.info("sentry_initialized environment=%s", settings.app_env)


def capture_exception(exc: BaseException, **context: Any) -> None:
    if not _sentry_enabled:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for key, value in context.items():
                if value is not None:
                    scope.set_extra(str(key), value)
            sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001
        logger.debug("sentry_capture_failed", exc_info=True)


def set_request_context(*, request_id: str | None = None, path: str | None = None) -> None:
    if not _sentry_enabled:
        return
    try:
        import sentry_sdk

        if request_id:
            sentry_sdk.set_tag("request_id", request_id)
        if path:
            sentry_sdk.set_tag("path", path)
    except Exception:  # noqa: BLE001
        pass
