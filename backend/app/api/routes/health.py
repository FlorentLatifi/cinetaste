from fastapi import APIRouter
from sqlalchemy import text

from app.api.schemas.common import HealthResponse, ReadyResponse
from app.core.config import get_settings
from app.infrastructure.db.redis import get_redis
from app.infrastructure.db.session import engine

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.app_name, env=settings.app_env)


@router.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse:
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

    status = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return ReadyResponse(status=status, database=db_status, redis=redis_status)
