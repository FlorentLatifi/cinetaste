"""Try CineTaste without an account.

A guest answers the same cards as onboarding and gets a ranked, explained
slate back. Nothing about them is stored: the answers arrive in the request,
are ranked by the same code as For You, and are gone when it returns. If they
then sign up, the SPA replays the answers into onboarding.

Both routes are public, so they sit behind their own rate-limit family (see
``RateLimitMiddleware``) and are shaped to be cheap: cards for the unfiltered
deck are cached, a slate is capped at 30 titles, identical answers are served
from a short-lived cache, and ranking itself runs under a process-wide
concurrency cap so a flood is refused (503 + Retry-After) rather than queued.
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
from app.application.guest_slates import GuestSlateCache, guest_slate_cache_key
from app.application.onboarding_service import (
    OnboardingService,
    count_ratings,
    normalize_reactions,
)
from app.application.popular_pool import get_popular_pool
from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.core.concurrency import get_guest_rank_gate
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
    service = RecommendationService(
        session,
        settings,
        popular_pool=get_popular_pool(settings.guest_popular_pool_ttl_seconds),
    )
    normalized = normalize_reactions(
        [{"title_id": str(r.title_id), "action": r.action} for r in body.reactions]
    )

    # Before any database work. Only slates that passed the gates below are ever
    # stored, so a hit is already known to be valid and needs no re-validation.
    cache = GuestSlateCache(get_store(), ttl_seconds=settings.guest_slate_cache_ttl_seconds)
    cache_key = guest_slate_cache_key(normalized, body.limit)
    cached = await cache.get(cache_key)
    if cached is not None:
        try:
            hydrated = await service.hydrate_slate_payload(cached)
        except Exception:  # noqa: BLE001
            logger.warning("guest_slate_cache_hydrate_failed key=%s", cache_key, exc_info=True)
            hydrated = []
        if hydrated:
            return _slate_out(hydrated)

    # Everything from here is database + CPU work, so it all runs under the cap. The
    # slot is taken *before* the first query: a request waiting for a slot must not
    # also be holding one of the pooled connections (15 per worker), or a flood
    # queues on the pool instead and the cap protects nothing.
    gate = get_guest_rank_gate(
        settings.guest_rank_max_concurrent, settings.guest_rank_max_wait_seconds
    )
    async with gate.slot():
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

    await cache.set(cache_key, service.slate_payload([item for _title, item in ranked]))
    return _slate_out(ranked)


def _slate_out(ranked: list) -> RecommendationSlateOut:
    return RecommendationSlateOut(
        items=[RecommendationItemOut.from_ranked(title, item) for title, item in ranked]
    )
