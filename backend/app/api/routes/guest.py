"""Try CineTaste without an account.

A guest answers the same cards as onboarding and gets a ranked, explained
slate back. Nothing about them is stored: the answers arrive in the request,
are ranked by the same code as For You, and are gone when it returns. If they
then sign up, the SPA replays the answers into onboarding.

Both routes are public, so they sit behind their own rate-limit family (see
``RateLimitMiddleware``) and are shaped to be cheap: cards for the unfiltered
deck are cached, and a slate is capped at 30 titles.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, get_settings_dep
from app.api.schemas.titles import (
    GuestRecommendationsRequest,
    OnboardingCardsOut,
    RecommendationItemOut,
    RecommendationSlateOut,
    TitleSummaryOut,
)
from app.application.onboarding_service import (
    OnboardingService,
    count_ratings,
    normalize_reactions,
)
from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.core.config import Settings
from app.domain.exceptions import AppError
from app.infrastructure.cache import get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/guest")

# Lower than onboarding's 6: a guest wants something to watch tonight, not a
# calibrated profile, and every extra card before the payoff loses people.
# More answers still sharpen the slate, and the SPA says so.
GUEST_MIN_RATINGS = 3
GUEST_MIN_POSITIVE = 1

# The unfiltered deck is the same for every guest, so one cached copy serves
# them all. Short enough that a fresh ingest shows up within minutes.
_CARDS_CACHE_TTL_SECONDS = 600


@router.get("/cards", response_model=OnboardingCardsOut)
async def guest_cards(
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(default=15, ge=8, le=40),
    exclude: Annotated[list[UUID] | None, Query(max_length=200)] = None,
) -> OnboardingCardsOut:
    """The onboarding deck, for someone without an account."""
    cache_key = f"guest:cards:{limit}" if not exclude else None
    store = get_store()
    if cache_key:
        cached = await store.get(cache_key)
        if cached:
            try:
                return OnboardingCardsOut.model_validate_json(cached)
            except Exception:  # noqa: BLE001
                logger.warning("guest_cards_cache_invalid key=%s", cache_key, exc_info=True)

    rec = RecommendationService(session, settings)
    cards = await OnboardingService(session, TasteService(session), rec).cards(
        limit=limit, exclude_ids=exclude
    )
    out = OnboardingCardsOut(items=[TitleSummaryOut.from_title(t) for t in cards])
    if cache_key:
        await store.set(
            cache_key, json.dumps(out.model_dump(mode="json")), ttl_seconds=_CARDS_CACHE_TTL_SECONDS
        )
    return out


@router.post("/recommendations", response_model=RecommendationSlateOut)
async def guest_recommendations(
    body: GuestRecommendationsRequest,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> RecommendationSlateOut:
    """Rank a slate from card answers without storing anything."""
    service = RecommendationService(session, settings)
    normalized = normalize_reactions(
        [{"title_id": str(r.title_id), "action": r.action} for r in body.reactions]
    )
    # Answers about titles we don't have can't shape a profile, so they must
    # not count toward the gate either.
    known = await service.existing_title_ids([title_id for title_id, _e in normalized])
    normalized = [(title_id, event) for title_id, event in normalized if title_id in known]

    rated, positive = count_ratings(normalized)
    if rated < GUEST_MIN_RATINGS:
        raise AppError(
            f"Rate at least {GUEST_MIN_RATINGS} titles you've seen. You've rated {rated}.",
            status_code=400,
            code="guest_insufficient_ratings",
        )
    if positive < GUEST_MIN_POSITIVE:
        raise AppError(
            "Mark at least one title you liked so we know what to look for.",
            status_code=400,
            code="guest_insufficient_positive",
        )

    ranked = await service.guest_slate(normalized, limit=body.limit)
    return RecommendationSlateOut(
        items=[RecommendationItemOut.from_ranked(title, item) for title, item in ranked]
    )
