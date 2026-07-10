from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.infrastructure.db.redis import get_redis

logger = logging.getLogger("cinetaste.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach request id + structured access log."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window rate limiting via Redis. Fails open if Redis is unavailable."""

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    def _limits_for(self, path: str) -> tuple[int, int]:
        """Return (max_requests, window_seconds)."""
        if path.endswith("/auth/login") or path.endswith("/auth/register"):
            return self._settings.rate_limit_auth_requests, self._settings.rate_limit_auth_window_seconds
        if "/auth/" in path:
            return self._settings.rate_limit_auth_requests * 2, self._settings.rate_limit_auth_window_seconds
        return self._settings.rate_limit_requests, self._settings.rate_limit_window_seconds

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        # Never rate-limit health probes
        if path.endswith("/health") or path.endswith("/ready"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        max_requests, window = self._limits_for(path)
        bucket = f"rl:{client_ip}:{path}:{window}"

        try:
            redis = await get_redis()
            current = await redis.incr(bucket)
            if current == 1:
                await redis.expire(bucket, window)
            if current > max_requests:
                ttl = await redis.ttl(bucket)
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "rate_limited",
                        "message": "Too many requests. Please slow down.",
                    },
                    headers={
                        "Retry-After": str(max(ttl, 1)),
                        "X-RateLimit-Limit": str(max_requests),
                    },
                )
        except Exception:
            logger.warning("rate_limit_unavailable path=%s", path, exc_info=True)

        return await call_next(request)
