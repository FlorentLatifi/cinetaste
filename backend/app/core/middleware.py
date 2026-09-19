from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.observability import set_request_context
from app.infrastructure.cache import get_store

logger = logging.getLogger("cinetaste.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach request id + structured access log."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_context(request_id=request_id, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "request_failed method=%s path=%s duration_ms=%.1f request_id=%s",
                request.method,
                request.url.path,
                duration_ms,
                request_id,
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        # Structured access line — scrapeable for basic RED metrics in log drains
        logger.info(
            "method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        # API is JSON; CSP is defensive for any accidental HTML
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


def client_ip(request: Request, *, trusted_proxy_hops: int = 0) -> str:
    """Client IP used as the rate-limit key.

    Each proxy appends the address it received the request from to
    X-Forwarded-For, so the entry ``trusted_proxy_hops`` from the right was
    written by our outermost trusted proxy. Entries further left come from the
    client and can be forged, so the left-most value is never trusted.
    """
    if trusted_proxy_hops > 0:
        raw = request.headers.get("x-forwarded-for", "")
        hops = [part.strip() for part in raw.split(",") if part.strip()]
        if hops:
            return hops[-trusted_proxy_hops] if len(hops) >= trusted_proxy_hops else hops[0]
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window rate limiting per client IP and route family.

    Counters live in Redis when configured, otherwise in process (see
    ``app.infrastructure.cache``). If the store itself fails, auth routes fail
    closed (brute-force protection) and other routes fail open.
    """

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    def _is_auth_path(self, path: str) -> bool:
        return "/auth/" in path

    def _limits_for(self, path: str) -> tuple[int, int]:
        """Return (max_requests, window_seconds)."""
        if path.endswith("/auth/login") or path.endswith("/auth/register"):
            return self._settings.rate_limit_auth_requests, self._settings.rate_limit_auth_window_seconds
        if path.endswith("/auth/forgot-password") or path.endswith("/auth/reset-password"):
            return self._settings.rate_limit_auth_requests, self._settings.rate_limit_auth_window_seconds
        if self._is_auth_path(path):
            return self._settings.rate_limit_auth_requests * 2, self._settings.rate_limit_auth_window_seconds
        return self._settings.rate_limit_requests, self._settings.rate_limit_window_seconds

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        # Never rate-limit health probes
        if path.endswith("/health") or path.endswith("/ready"):
            return await call_next(request)

        ip = client_ip(request, trusted_proxy_hops=self._settings.trusted_proxy_hops)
        max_requests, window = self._limits_for(path)
        # Bucket by route family, not full path+query, to limit cardinality.
        family = "auth" if self._is_auth_path(path) else "api"
        bucket = f"rl:{ip}:{family}:{window}"

        try:
            current, retry_after = await get_store().hit(bucket, window_seconds=window)
        except Exception:
            logger.warning("rate_limit_unavailable path=%s", path, exc_info=True)
            if self._is_auth_path(path):
                return JSONResponse(
                    status_code=503,
                    content={
                        "code": "rate_limit_unavailable",
                        "message": "Authentication temporarily unavailable. Try again shortly.",
                    },
                    headers={"Retry-After": "5"},
                )
            return await call_next(request)

        if current > max_requests:
            return JSONResponse(
                status_code=429,
                content={
                    "code": "rate_limited",
                    "message": "Too many requests. Please slow down.",
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_requests),
                },
            )
        return await call_next(request)
