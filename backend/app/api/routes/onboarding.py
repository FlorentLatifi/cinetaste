from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_settings_dep
from app.api.schemas.auth import UserResponse
from app.api.schemas.titles import OnboardingCardsOut, OnboardingCompleteRequest, TitleSummaryOut
from app.application.onboarding_service import OnboardingService
from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.core.config import Settings
from app.infrastructure.db.session import get_db

router = APIRouter(prefix="/onboarding")


@router.get("/cards", response_model=OnboardingCardsOut)
async def onboarding_cards(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(default=24, ge=8, le=40),
    exclude: list[UUID] | None = Query(
        default=None,
        description="Title IDs already shown (e.g. after many Haven't seen answers).",
    ),
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
    session: Annotated[AsyncSession, Depends(get_db)],
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
