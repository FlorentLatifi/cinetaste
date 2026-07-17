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
    """Readiness: DB must be up. Redis is optional (caching only).

    Returns HTTP 503 when DB is down so load balancers drain the instance.
    """
    db_status = "ok"
    redis_status = "unavailable"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        redis = await get_redis()
        pong = await redis.ping()
        redis_status = "ok" if pong else "error"
    except Exception:
        redis_status = "unavailable"

    db_ok = db_status == "ok"
    body = ReadyResponse(status="ok" if db_ok else "not_ready", database=db_status, redis=redis_status)
    if not db_ok:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
