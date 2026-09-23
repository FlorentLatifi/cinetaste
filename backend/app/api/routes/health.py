import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.schemas.common import HealthResponse, ReadyResponse
from app.core.config import get_settings
from app.infrastructure.cache import get_store
from app.infrastructure.db.session import engine

router = APIRouter()
logger = logging.getLogger(__name__)


async def _catalog_state(conn) -> str:
    """Is there a catalog, and has it been embedded?

    Two EXISTS probes rather than counts: they stop at the first row, so
    this stays cheap enough to run on every readiness check.
    """
    has_titles = await conn.scalar(text("SELECT EXISTS (SELECT 1 FROM titles)"))
    if not has_titles:
        return "empty"
    has_embeddings = await conn.scalar(
        text("SELECT EXISTS (SELECT 1 FROM titles WHERE embedding IS NOT NULL)")
    )
    return "ok" if has_embeddings else "unembedded"


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
    catalog = "unknown"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            catalog = await _catalog_state(conn)
    except Exception:
        db_status = "error"

    if catalog == "unembedded":
        # Not a readiness failure: refusing traffic would also block the
        # shell you would use to fix it. Loud in the logs instead.
        logger.warning(
            "catalog_unembedded — every title has embedding IS NULL, so For You will be empty. Run: python -m app.scripts.reembed_catalog"
        )

    store = get_store()
    try:
        await store.ping()
    except Exception:  # noqa: BLE001 — cache is optional; report it, don't fail
        pass

    db_ok = db_status == "ok"
    body = ReadyResponse(
        status="ok" if db_ok else "not_ready",
        database=db_status,
        cache=store.backend,
        catalog=catalog,
    )
    if not db_ok:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
