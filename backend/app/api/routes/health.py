from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.schemas.common import HealthResponse, ReadyResponse
from app.core.config import get_settings
from app.infrastructure.cache import get_store
from app.infrastructure.db.session import engine

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: process is up. Does not check dependencies."""
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.app_name, env=settings.app_env)


@router.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse | JSONResponse:
    """Readiness: DB must be up. The cache is optional (Redis or in-process).

    Returns HTTP 503 when DB is down so load balancers drain the instance.
    """
    db_status = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    store = get_store()
    try:
        await store.ping()
    except Exception:  # noqa: BLE001 — cache is optional; report it, don't fail
        pass

    db_ok = db_status == "ok"
    body = ReadyResponse(
        status="ok" if db_ok else "not_ready", database=db_status, cache=store.backend
    )
    if not db_ok:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
