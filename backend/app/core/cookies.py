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


def set_refresh_cookie(response: Response, raw_refresh: str, settings: Settings) -> None:
    """Set rotating refresh token as HttpOnly cookie."""
    # Cross-site SPA (e.g. Vercel → Render) needs SameSite=None + Secure.
    # Same-site local (localhost:5173 → localhost:8000) uses Lax without Secure.
    cross_site = settings.is_production
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_refresh,
        httponly=True,
        secure=cross_site or settings.cookie_secure,
        samesite="none" if cross_site else "lax",
        max_age=int(settings.jwt_refresh_ttl_days) * 24 * 60 * 60,
        path=refresh_cookie_path(settings),
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=refresh_cookie_path(settings),
        secure=settings.is_production or settings.cookie_secure,
        httponly=True,
        samesite="none" if settings.is_production else "lax",
    )
