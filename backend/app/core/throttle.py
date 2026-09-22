"""Per-identity throttling for credential endpoints.

The middleware limit in ``app.core.middleware`` is keyed by client IP, and an
IP is only as trustworthy as the proxy chain in front of the app. Our origin
host stays publicly reachable, so a caller can skip the CDN, forge
``X-Forwarded-For`` and pick its own bucket. It also does nothing against an
attacker spread over many addresses.

This module adds a second counter keyed by the *account* being attacked, which
neither of those tricks changes. Both limits apply; either one can reject.

Only failures are counted. Someone who keeps signing in correctly is never
locked out, while an attacker — whose attempts are failures by definition —
runs out of budget. The identifier is hashed before it becomes a key so no
email address lands in Redis or in a log line.
"""

from __future__ import annotations

import hashlib
import logging

from app.core.config import Settings
from app.domain.exceptions import AppError, RateLimitedError
from app.infrastructure.cache import get_rate_limit_store

logger = logging.getLogger(__name__)


def _key(scope: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()
    return f"rl:acct:{scope}:{digest[:32]}"


async def guard_identity(identifier: str, *, scope: str, settings: Settings) -> None:
    """Reject the request when this identity is out of failure budget.

    Fails **closed**: if the counter store is unreachable we cannot tell an
    attacker from a legitimate user, and an open door on a credential endpoint
    is worse than a short outage. The IP limiter makes the same call.
    """
    if not settings.rate_limit_enabled:
        return

    window = settings.rate_limit_account_window_seconds
    try:
        raw = await get_rate_limit_store().get(_key(scope, identifier))
    except Exception as exc:  # noqa: BLE001 — store down; do not guess
        logger.warning("account_throttle_unavailable scope=%s", scope, exc_info=True)
        raise AppError(
            "Authentication temporarily unavailable. Try again shortly.",
            status_code=503,
            code="rate_limit_unavailable",
        ) from exc

    if raw is not None and int(raw) >= settings.rate_limit_account_failures:
        # Deliberately the same wording for every scope: the response must not
        # confirm that the address belongs to an account.
        raise RateLimitedError(retry_after=window)


async def record_attempt(identifier: str, *, scope: str, settings: Settings) -> None:
    """Count one attempt against this identity.

    Fails **open**: the attempt has already been rejected, and losing a counter
    increment must never turn into a 500 on a login that correctly said no.
    """
    if not settings.rate_limit_enabled:
        return
    try:
        await get_rate_limit_store().hit(
            _key(scope, identifier),
            window_seconds=settings.rate_limit_account_window_seconds,
        )
    except Exception:  # noqa: BLE001
        logger.warning("account_throttle_write_failed scope=%s", scope, exc_info=True)
