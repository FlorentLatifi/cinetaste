from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.schemas.common import HealthResponse, ReadyResponse
from app.core.config import get_settings
from app.infrastructure.db.redis import get_redis
from app.infrastructure.db.session import engine

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: process is up. Does not check dependencies."""
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.app_name, env=settings.app_env)


@router.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse | JSONResponse:
    """Readiness: DB must be up. Redis is required for rate limits / cache health.

    Returns HTTP 503 when not fully ready so load balancers drain the instance.
    """
    db_status = "ok"
    redis_status = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        redis = await get_redis()
        pong = await redis.ping()
        if not pong:
            redis_status = "error"
    except Exception:
        redis_status = "error"

    # Database is hard-required. Redis error also marks not-ready in production
    # so rate-limit fail-closed auth path is honest; local can still run degraded
    # if needed via APP_ENV=local (soft redis for ready).
    settings = get_settings()
    db_ok = db_status == "ok"
    redis_ok = redis_status == "ok"
    if settings.is_production:
        fully_ready = db_ok and redis_ok
    else:
        # Local/dev: API is usable without Redis (recs compute cold; auth RL soft).
        fully_ready = db_ok

    status = "ok" if fully_ready else "not_ready"
    body = ReadyResponse(status=status, database=db_status, redis=redis_status)
    if not fully_ready:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
