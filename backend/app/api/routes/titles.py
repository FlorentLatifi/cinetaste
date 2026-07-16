from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_settings_dep
from app.api.schemas.titles import (
    InteractionRequest,
    ProviderOfferOut,
    ReasonOut,
    RecommendationItemOut,
    RecommendationSlateOut,
    TitleDetailOut,
    TitleSummaryOut,
    WhereToWatchOut,
)
from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.application.watch_providers import WatchProvidersService
from app.core.config import Settings
from app.domain.exceptions import NotFoundError
from app.infrastructure.db.session import get_db

router = APIRouter()


def _rec_service(session: AsyncSession, settings: Settings) -> RecommendationService:
    return RecommendationService(session, settings)


@router.get("/recommendations/for-you", response_model=RecommendationSlateOut)
async def for_you(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(default=20, ge=1, le=50),
) -> RecommendationSlateOut:
    service = _rec_service(session, settings)
    ranked = await service.for_you(user.id, limit=limit)
    items = [
        RecommendationItemOut(
            title=TitleSummaryOut.from_title(title),
            score=round(item.score, 4),
            reasons=[
                ReasonOut(code=r.code, message=r.message, evidence=r.evidence) for r in item.reasons
            ],
        )
        for title, item in ranked
    ]
    return RecommendationSlateOut(items=items)


@router.get("/titles/search", response_model=list[TitleSummaryOut])
async def search_titles(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[TitleSummaryOut]:
    service = _rec_service(session, settings)
    titles = await service.search(q, limit=limit)
    return [TitleSummaryOut.from_title(t) for t in titles]


@router.get("/titles/{title_id}/similar", response_model=list[TitleSummaryOut])
async def similar_titles(
    title_id: UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(default=12, ge=1, le=30),
) -> list[TitleSummaryOut]:
    service = _rec_service(session, settings)
    source = await service.get_title(title_id)
    if source is None:
        raise NotFoundError("Title not found")
    titles = await service.similar_titles(title_id, limit=limit)
    return [TitleSummaryOut.from_title(t) for t in titles]


@router.get("/titles/{title_id}", response_model=TitleDetailOut)
async def get_title(
    title_id: UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TitleDetailOut:
    service = _rec_service(session, settings)
    title = await service.get_title(title_id)
    if title is None:
        raise NotFoundError("Title not found")
    return TitleDetailOut.from_title_detail(title)


@router.get("/titles/{title_id}/where-to-watch", response_model=WhereToWatchOut)
async def where_to_watch(
    title_id: UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    region: str | None = Query(
        default=None,
        min_length=2,
        max_length=2,
        description="ISO-3166-1 alpha-2 country code (e.g. US, GB, DE)",
    ),
) -> WhereToWatchOut:
    """Streaming / rent / buy options for a title in one region.

    Data from TMDb (JustWatch). Soft-fails to ``available=false`` when TMDb
    is unconfigured or unreachable so the detail page still works.
    """
    service = WatchProvidersService(session, settings)
    result = await service.for_title(title_id, region=region)

    def map_offers(rows: list) -> list[ProviderOfferOut]:
        return [
            ProviderOfferOut(
                provider_id=p.provider_id,
                name=p.name,
                logo_url=p.logo_url,
                display_priority=p.display_priority,
            )
            for p in rows
        ]

    return WhereToWatchOut(
        region=result.region,
        link=result.link,
        flatrate=map_offers(result.flatrate),
        free=map_offers(result.free),
        ads=map_offers(result.ads),
        rent=map_offers(result.rent),
        buy=map_offers(result.buy),
        available=result.available,
        attribution=result.attribution,
        source=result.source,
    )


@router.post("/titles/{title_id}/interactions", status_code=204)
async def interact(
    title_id: UUID,
    body: InteractionRequest,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    service = _rec_service(session, settings)
    title = await service.get_title(title_id)
    if title is None:
        raise NotFoundError("Title not found")

    taste = TasteService(session)
    await taste.record_interaction(
        user_id=user.id,
        title_id=title_id,
        event_type=body.event_type,
    )
    await service.invalidate_user(user.id)


@router.get("/watchlist", response_model=list[TitleSummaryOut])
async def watchlist(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> list[TitleSummaryOut]:
    service = _rec_service(session, settings)
    titles = await service.watchlist(user.id)
    return [TitleSummaryOut.from_title(t) for t in titles]
