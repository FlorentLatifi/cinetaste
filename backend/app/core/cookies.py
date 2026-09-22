"""httpOnly refresh-token cookies.

Refresh tokens never go to localStorage. The SPA keeps only short-lived access
JWTs in memory and renews them via ``POST /auth/refresh`` with credentials.
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import Settings

REFRESH_COOKIE_NAME = "ct_refresh"


def refresh_cookie_path(settings: Settings) -> str:
    # Scoped to auth routes only — not sent on every API call.
    return f"{settings.api_prefix.rstrip('/')}/auth"


def _secure(settings: Settings) -> bool:
    # SameSite=None is only accepted by browsers together with Secure.
    return settings.is_production or settings.cookie_secure or settings.cookie_samesite == "none"


def set_refresh_cookie(response: Response, raw_refresh: str, settings: Settings) -> None:
    """Set rotating refresh token as HttpOnly cookie.

    Default SameSite=Lax assumes the SPA reaches the API on the same site
    (Vite proxy locally, Vercel rewrite in production), which keeps the cookie
    first-party so Safari's third-party cookie blocking doesn't log users out.
    """
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_refresh,
        httponly=True,
        secure=_secure(settings),
        samesite=settings.cookie_samesite,
        max_age=int(settings.jwt_refresh_ttl_days) * 24 * 60 * 60,
        path=refresh_cookie_path(settings),
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=refresh_cookie_path(settings),
        secure=_secure(settings),
        httponly=True,
        samesite=settings.cookie_samesite,
    )
