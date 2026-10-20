from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.observability import set_request_context
from app.infrastructure.cache import get_rate_limit_store

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
    def __init__(self, app, settings: Settings | None = None) -> None:
        super().__init__(app)
        self._settings = settings

    def _https(self, request: Request) -> bool:
        """Is this request served over TLS?

        ``request.url.scheme`` is only rewritten from X-Forwarded-Proto when
        uvicorn trusts the peer (``--forwarded-allow-ips``). Behind a platform
        proxy whose address we do not know that never happens and the scheme
        stays http — which silently dropped HSTS in production. The
        environment is the reliable signal: production is always behind TLS.
        """
        if self._settings is not None and self._settings.is_production:
            return True
        return request.url.scheme == "https"

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
        if self._https(request):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


def _parsed_ip(value: str) -> str | None:
    """Normalised address, or None when this is not one.

    Accepts ``1.2.3.4:5678`` and ``[::1]:443`` (some proxies append a port) and
    drops an IPv6 zone id. Anything else is a client-written string rather than
    an address and must never become a bucket key.
    """
    candidate = value.strip()
    if candidate.startswith("["):  # [::1]:443
        candidate = candidate[1:].split("]", 1)[0]
    elif candidate.count(":") == 1:  # 1.2.3.4:5678
        candidate = candidate.rsplit(":", 1)[0]
    candidate = candidate.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def client_ip(request: Request, *, trusted_proxy_hops: int = 0) -> str:
    """Client IP used as the rate-limit key.

    Each proxy appends the address it received the request from to
    X-Forwarded-For, so the entry ``trusted_proxy_hops`` from the right was
    written by our outermost trusted proxy. Entries further left come from the
    client and can be forged, so the left-most value is never trusted.

    Two ways that reasoning breaks, both handled here:

    * **Short chain.** The origin host stays publicly reachable, so a request
      can arrive through fewer proxies than configured. Every entry is then
      client-written and the socket peer is the only honest value. The old code
      fell back to ``hops[0]`` here, handing the caller its own bucket key.
    * **Not an address.** A forged entry can be any string; rejecting
      non-addresses keeps junk out of the store and out of logs.

    Even so, a caller that reaches the origin directly *and* forges a
    full-length chain still picks its own bucket. That is why everything worth
    brute-forcing is also throttled per account — see ``app.core.throttle``.
    """
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer

    raw = request.headers.get("x-forwarded-for", "")
    hops = [part.strip() for part in raw.split(",") if part.strip()]
    if len(hops) < trusted_proxy_hops:
        return peer
    return _parsed_ip(hops[-trusted_proxy_hops]) or peer


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

    def _limits_for(self, path: str) -> tuple[int, int, str]:
        """Return (max_requests, window_seconds, bucket_family).

        The family is part of the key, so families with different ceilings must
        not share a counter: routine ``/auth/refresh`` traffic used to spend the
        login budget, and a login flood used to lock out refreshes.
        """
        auth_window = self._settings.rate_limit_auth_window_seconds
        if path.endswith("/auth/login") or path.endswith("/auth/register"):
            return self._settings.rate_limit_auth_requests, auth_window, "auth_login"
        if path.endswith("/auth/forgot-password") or path.endswith("/auth/reset-password"):
            return self._settings.rate_limit_auth_requests, auth_window, "auth_reset"
        if self._is_auth_path(path):
            return self._settings.rate_limit_auth_requests * 2, auth_window, "auth_other"
        if "/guest/" in path:
            return (
                self._settings.rate_limit_guest_requests,
                self._settings.rate_limit_window_seconds,
                "guest",
            )
        return (
            self._settings.rate_limit_requests,
            self._settings.rate_limit_window_seconds,
            "api",
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        # Never rate-limit health probes
        if path.endswith("/health") or path.endswith("/ready"):
            return await call_next(request)

        ip = client_ip(request, trusted_proxy_hops=self._settings.trusted_proxy_hops)
        # Bucket by route family, not full path+query, to limit cardinality.
        max_requests, window, family = self._limits_for(path)
        bucket = f"rl:{ip}:{family}:{window}"

        try:
            store = get_rate_limit_store()
            current, retry_after = await store.hit(bucket, window_seconds=window)
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


class BodySizeLimitMiddleware:
    """Refuse request bodies larger than ``max_bytes`` before anything parses them.

    Without it a public route read and parsed whatever it was sent: a 4 MB JSON
    body to /guest/recommendations was decoded in full before validation said
    no, so a handful of 100 MB requests could exhaust a small instance's
    memory. The largest legitimate body (a taste-snapshot import) is a few
    tens of kilobytes.

    A declared Content-Length over the limit is refused without reading. A
    body without one (chunked) is read up to the limit and then refused, so
    either way at most ``max_bytes`` is ever held. Pure ASGI rather than
    BaseHTTPMiddleware, because the point is to act before the body is read.
    """

    _METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in self._METHODS:
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = False  # let the server reject a malformed header
            if too_big:
                await self._reject(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        # No declared length: buffer up to the limit, then replay.
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, scope, receive, send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "code": "payload_too_large",
                "message": f"Request body is larger than {self.max_bytes // 1024} KB.",
            },
        )
        await response(scope, receive, send)
