from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.domain.taste_signals import (
    POSITIVE_RATING_EVENT_TYPES,
    RATING_EVENT_TYPES,
    is_supported_event,
)
from app.domain.exceptions import AppError
from app.infrastructure.db.models.user import User

# Minimum explicit 1–4 ratings before we trust the profile enough for For You.
MIN_ONBOARDING_RATINGS = 6
# At least some positive affinity so the slate is not pure "avoid these".
MIN_ONBOARDING_POSITIVE = 2

# Actions accepted on /onboarding/complete (see docs/TASTE_SIGNALS.md).
ONBOARDING_ACTIONS = frozenset(
    {
        "haven't_seen",  # zero taste signal
        "not_interested",  # mild negative
        "rate_1",  # Bad
        "rate_2",  # It's ok
        "rate_3",  # Good
        "rate_4",  # Favorite
        # Legacy aliases (older clients) → mapped below
        "like",
        "dislike",
    }
)

# Map legacy like/dislike → rating steps if still sent.
_LEGACY_ACTION_MAP = {
    "like": "rate_3",
    "dislike": "rate_1",
}


class OnboardingService:
    def __init__(
        self,
        session: AsyncSession,
        taste: TasteService,
        recommendations: RecommendationService,
    ) -> None:
        self._session = session
        self._taste = taste
        self._recommendations = recommendations

    async def cards(
        self,
        limit: int = 15,
        *,
        exclude_ids: list[UUID] | None = None,
    ):
        """Return cold-start cards (curated seed deck; see onboarding_seed_deck.json)."""
        cards = await self._recommendations.onboarding_cards(
            limit=limit,
            exclude_ids=set(exclude_ids or []),
        )
        if len(cards) < 8 and not exclude_ids:
            raise AppError(
                "Catalog is empty or missing seed titles. Run catalog ingest first "
                "(python -m app.scripts.ingest_catalog) so the onboarding seed deck "
                "is available.",
                status_code=503,
                code="catalog_empty",
            )
        return cards

    async def complete(
        self,
        user: User,
        reactions: list[dict[str, str]],
    ) -> User:
        """Persist onboarding reactions and gate on enough real ratings.

        - ``haven't_seen`` → weight 0 (no taste signal), state for analytics only
        - ``not_interested`` → mild negative signal
        - ``rate_1``…``rate_4`` → Bad … Favorite scale
        """
        # Validate first so we never leave partial interactions when gates fail.
        normalized: list[tuple[UUID, str]] = []
        rated = 0
        positive = 0

        for reaction in reactions:
            action = reaction["action"]
            if action not in ONBOARDING_ACTIONS:
                continue
            event_type = _LEGACY_ACTION_MAP.get(action, action)
            if not is_supported_event(event_type):
                continue
            title_id = UUID(reaction["title_id"])
            normalized.append((title_id, event_type))
            if event_type in RATING_EVENT_TYPES:
                rated += 1
            if event_type in POSITIVE_RATING_EVENT_TYPES:
                positive += 1

        if not normalized:
            raise AppError(
                "No valid reactions provided.",
                status_code=400,
                code="onboarding_empty",
            )

        if rated < MIN_ONBOARDING_RATINGS:
            raise AppError(
                f"Rate at least {MIN_ONBOARDING_RATINGS} titles you've seen "
                f"(Bad → Favorite). You've rated {rated}. "
                "Use “Haven't seen it” for unfamiliar titles — those don't count.",
                status_code=400,
                code="onboarding_insufficient_ratings",
            )

        if positive < MIN_ONBOARDING_POSITIVE:
            raise AppError(
                f"Mark at least {MIN_ONBOARDING_POSITIVE} titles as It's ok, Good, or Favorite "
                "so we can recommend things you might like.",
                status_code=400,
                code="onboarding_insufficient_positive",
            )

        # Persist all signals, then recompute the profile once (not per card).
        for title_id, event_type in normalized:
            await self._taste.record_interaction(
                user_id=user.id,
                title_id=title_id,
                event_type=event_type,
                recompute=False,
            )
        await self._taste.recompute_profile(user.id)

        user.onboarding_completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._recommendations.invalidate_user(user.id)
        return user
