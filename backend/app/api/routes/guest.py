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

from fastapi import APIRouter, Depends, Query, Request

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
from app.core.concurrency import ServiceBusyError, get_guest_rank_gate
from app.core.config import Settings
from app.core.singleflight import SingleFlight
from app.core.timing import PhaseTimer, phase, start_timer, stop_timer
from app.domain.exceptions import AppError
from app.infrastructure.cache import get_store

logger = logging.getLogger(__name__)
# One timing line per request lands here (see app.core.timing for how to read it).
timing_logger = logging.getLogger("cinetaste.guest.timing")

router = APIRouter(prefix="/guest")

# Lower than onboarding's 6: a guest wants something to watch tonight, not a
# calibrated profile, and every extra card before the payoff loses people.
# More answers still sharpen the slate, and the SPA says so.
GUEST_MIN_RATINGS = 3
GUEST_MIN_POSITIVE = 1

# The unfiltered deck is the same for every guest, so one cached copy serves
# them all. Short enough that a fresh ingest shows up within minutes.
_CARDS_CACHE_TTL_SECONDS = 600

# Followers wait at most this long for the request computing the same thing before doing
# the work themselves. A cold deck or slate takes seconds on a slow database, and the
# connection pool gives up after 5 s, so a leader that is not done by then is not coming
# back soon.
_FLIGHT_WAIT_SECONDS = 10.0
_cards_flight: SingleFlight[OnboardingCardsOut] = SingleFlight()
# Keyed by the slate cache key, so a different limit or different answers never share.
# Shares the serialized response, never ORM objects: the leader's session may roll back or
# close while a follower is still reading.
_slate_flight: SingleFlight[RecommendationSlateOut] = SingleFlight()


@router.get("/cards", response_model=OnboardingCardsOut)
async def guest_cards(
    request: Request,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(default=15, ge=8, le=40),
    exclude: Annotated[list[UUID] | None, Query(max_length=200)] = None,
) -> OnboardingCardsOut:
    """The onboarding deck, for someone without an account."""
    timer, token = start_timer()
    outcome = "error"
    try:
        out, outcome = await _cards(session, settings, limit, exclude)
        return out
    except AppError:
        outcome = "rejected"
        raise
    finally:
        stop_timer(token)
        _log_timing(timer, "guest_cards_timing", request, outcome, limit=limit)


async def _cards(
    session: DbSession, settings: Settings, limit: int, exclude: list[UUID] | None
) -> tuple[OnboardingCardsOut, str]:
    async def build() -> OnboardingCardsOut:
        rec = RecommendationService(session, settings)
        with phase("cards"):
            cards = await OnboardingService(session, TasteService(session), rec).cards(
                limit=limit, exclude_ids=exclude
            )
        return OnboardingCardsOut(items=[TitleSummaryOut.from_title(t) for t in cards])

    if exclude:
        # A page of "everything but these": different for every caller, so there is
        # nothing to share or store.
        return await build(), "uncached"

    cache_key = f"guest:cards:{limit}"
    store = get_store()

    async def lookup() -> OnboardingCardsOut | None:
        with phase("cache"):
            cached = await store.get(cache_key)
        if not cached:
            return None
        try:
            return OnboardingCardsOut.model_validate_json(cached)
        except Exception:  # noqa: BLE001
            logger.warning("guest_cards_cache_invalid key=%s", cache_key, exc_info=True)
            return None

    async def compute() -> OnboardingCardsOut:
        out = await build()
        await store.set(
            cache_key, json.dumps(out.model_dump(mode="json")), ttl_seconds=_CARDS_CACHE_TTL_SECONDS
        )
        return out

    # Many visitors landing on a cold or just-expired deck at once would each run the
    # same query and hold a pooled connection for it. One computes; the rest wait for
    # that result (see app.core.singleflight for how failures are handled).
    out, how = await _cards_flight.do(
        cache_key, lookup=lookup, compute=compute, wait_seconds=_FLIGHT_WAIT_SECONDS
    )
    # "computed" is this request doing the work; "shared" is waiting for someone else's.
    return out, {"hit": "hit", "computed": "miss", "shared": "shared", "fallback": "miss"}[how]


@router.post("/recommendations", response_model=RecommendationSlateOut)
async def guest_recommendations(
    request: Request,
    body: GuestRecommendationsRequest,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> RecommendationSlateOut:
    """Rank a slate from card answers without storing anything."""
    timer, token = start_timer()
    outcome = "error"
    try:
        out, outcome = await _recommendations(body, session, settings)
        return out
    except ServiceBusyError:
        outcome = "busy"
        raise
    except AppError:
        outcome = "rejected"
        raise
    finally:
        stop_timer(token)
        _log_timing(
            timer,
            "guest_slate_timing",
            request,
            outcome,
            limit=body.limit,
            reactions=len(body.reactions),
        )


async def _recommendations(
    body: GuestRecommendationsRequest, session: DbSession, settings: Settings
) -> tuple[RecommendationSlateOut, str]:
    service = RecommendationService(
        session,
        settings,
        popular_pool=get_popular_pool(settings.guest_popular_pool_ttl_seconds),
    )
    normalized = normalize_reactions(
        [{"title_id": str(r.title_id), "action": r.action} for r in body.reactions]
    )
    cache = GuestSlateCache(get_store(), ttl_seconds=settings.guest_slate_cache_ttl_seconds)
    cache_key = guest_slate_cache_key(normalized, body.limit)

    async def lookup() -> RecommendationSlateOut | None:
        # Before any database work. Only slates that passed the gates in ``compute`` are
        # ever stored, so a hit is already known to be valid and needs no re-validation.
        with phase("cache"):
            cached = await cache.get(cache_key)
            if cached is None:
                return None
            try:
                hydrated = await service.hydrate_slate_payload(cached)
            except Exception:  # noqa: BLE001
                logger.warning("guest_slate_cache_hydrate_failed key=%s", cache_key, exc_info=True)
                return None
        return _slate_out(hydrated) if hydrated else None

    async def compute() -> RecommendationSlateOut:
        # Everything here is database + CPU work, so it all runs under the cap. The slot
        # is taken *before* the first query: a request waiting for a slot must not also
        # be holding one of the pooled connections (15 per worker), or a flood queues on
        # the pool instead and the cap protects nothing.
        gate = get_guest_rank_gate(
            settings.guest_rank_max_concurrent, settings.guest_rank_max_wait_seconds
        )
        async with gate.slot():
            with phase("validate"):
                # Answers about titles we don't have can't shape a profile, so they must
                # not count toward the gate either.
                known = await service.existing_title_ids([t for t, _e in normalized])
                answers = [(t, event) for t, event in normalized if t in known]

                rated, positive = count_ratings(answers)
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

            ranked = await service.guest_slate(answers, limit=body.limit)

        with phase("cache_write"):
            await cache.set(cache_key, service.slate_payload([item for _title, item in ranked]))
        with phase("serialize"):
            return _slate_out(ranked)

    # Identical answers arriving together (a shared link, the SPA replaying them, a
    # double-fired request) are one piece of work: one request ranks, the rest wait for
    # its result instead of each taking a rank slot and a pooled connection. Failure is
    # never shared (see app.core.singleflight): a follower of a failed leader does the work
    # itself, exactly as it would have without this.
    out, how = await _slate_flight.do(
        cache_key, lookup=lookup, compute=compute, wait_seconds=_FLIGHT_WAIT_SECONDS
    )
    return out, {"hit": "hit", "computed": "miss", "shared": "shared", "fallback": "miss"}[how]


def _slate_out(ranked: list) -> RecommendationSlateOut:
    return RecommendationSlateOut(
        items=[RecommendationItemOut.from_ranked(title, item) for title, item in ranked]
    )


def _log_timing(
    timer: PhaseTimer, event_name: str, request: Request, outcome: str, **fields: object
) -> None:
    # Never let diagnostics break a request.
    try:
        timer.log(
            timing_logger,
            event_name,
            outcome=outcome,
            request_id=getattr(request.state, "request_id", None),
            **fields,
        )
    except Exception:  # noqa: BLE001
        logger.debug("guest_timing_log_failed", exc_info=True)
