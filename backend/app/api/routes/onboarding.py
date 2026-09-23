from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, get_settings_dep
from app.api.schemas.auth import UserResponse
from app.api.schemas.titles import OnboardingCardsOut, OnboardingCompleteRequest, TitleSummaryOut
from app.application.onboarding_service import OnboardingService
from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.core.config import Settings

router = APIRouter(prefix="/onboarding")


@router.get("/cards", response_model=OnboardingCardsOut)
async def onboarding_cards(
    user: CurrentUser,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(
        default=15,
        ge=8,
        le=40,
        description="Cards to return. First batch uses the curated primary seed deck (~15).",
    ),
    exclude: Annotated[
        list[UUID] | None,
        Query(
            max_length=200,
            description=(
                "Title IDs already shown (e.g. after many Haven't seen answers). "
                "Bounded: the seed deck is ~15 cards, so a longer list is a client bug "
                "or someone probing how large a query we will build."
            ),
        ),
    ] = None,
) -> OnboardingCardsOut:
    rec = RecommendationService(session, settings)
    taste = TasteService(session)
    service = OnboardingService(session, taste, rec)
    cards = await service.cards(limit=limit, exclude_ids=exclude)
    return OnboardingCardsOut(items=[TitleSummaryOut.from_title(t) for t in cards])


@router.post("/complete", response_model=UserResponse)
async def complete_onboarding(
    body: OnboardingCompleteRequest,
    user: CurrentUser,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> UserResponse:
    rec = RecommendationService(session, settings)
    taste = TasteService(session)
    service = OnboardingService(session, taste, rec)
    updated = await service.complete(
        user,
        [{"title_id": str(r.title_id), "action": r.action} for r in body.reactions],
    )
    return UserResponse.model_validate(updated)
