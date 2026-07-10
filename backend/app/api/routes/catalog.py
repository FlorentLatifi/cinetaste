from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_settings_dep
from app.api.schemas.titles import CatalogStatusOut
from app.application.catalog_ingest import CatalogIngestService
from app.core.config import Settings
from app.domain.exceptions import AppError, ForbiddenError
from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.session import get_db
from app.infrastructure.tmdb.client import TmdbClient

router = APIRouter(prefix="/catalog")


@router.get("/status", response_model=CatalogStatusOut)
async def catalog_status(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CatalogStatusOut:
    total = await session.scalar(select(func.count()).select_from(Title)) or 0
    with_emb = (
        await session.scalar(
            select(func.count()).select_from(Title).where(Title.embedding.is_not(None))
        )
        or 0
    )
    return CatalogStatusOut(
        title_count=int(total),
        with_embeddings=int(with_emb),
        ready_for_onboarding=int(with_emb) >= 8,
    )


@router.post("/ingest")
async def ingest_catalog(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    pages: int = Query(default=2, ge=1, le=5),
    include_tv: bool = True,
) -> dict:
    """Admin-style ingest endpoint for local/dev. Protected by auth; restrict further in prod."""
    if settings.is_production:
        raise ForbiddenError("Catalog ingest via API is disabled in production")

    try:
        tmdb = TmdbClient(settings)
    except AppError:
        raise
    try:
        service = CatalogIngestService(session, tmdb)
        stats = await service.ingest_popular(pages=pages, include_tv=include_tv)
        return {"status": "ok", **stats}
    finally:
        await tmdb.aclose()
