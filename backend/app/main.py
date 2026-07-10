from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.domain.exceptions import AppError
from app.infrastructure.db.redis import close_redis, get_redis


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    try:
        await get_redis()
    except Exception:
        pass
    yield
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )

    # Order: last added = outermost for BaseHTTPMiddleware-style stacks
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    if settings.is_production and settings.trusted_hosts.strip():
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        content = {"code": exc.code, "message": exc.message}
        if request_id:
            content["request_id"] = request_id
        return JSONResponse(status_code=exc.status_code, content=content)

    if not settings.app_debug:

        @app.exception_handler(Exception)
        async def unhandled_error_handler(request: Request, _exc: Exception) -> JSONResponse:
            request_id = getattr(request.state, "request_id", None)
            return JSONResponse(
                status_code=500,
                content={
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "request_id": request_id,
                },
            )

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
