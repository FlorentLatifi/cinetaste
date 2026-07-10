from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.domain.exceptions import AppError
from app.infrastructure.db.models.user import User


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

    async def cards(self, limit: int = 18):
        cards = await self._recommendations.onboarding_cards(limit=limit)
        if len(cards) < 8:
            raise AppError(
                "Catalog is empty. Run catalog ingest first "
                "(python -m app.scripts.ingest_catalog).",
                status_code=503,
                code="catalog_empty",
            )
        return cards

    async def complete(
        self,
        user: User,
        reactions: list[dict[str, str]],
    ) -> User:
        likes = 0
        for reaction in reactions:
            title_id = UUID(reaction["title_id"])
            action = reaction["action"]
            if action not in {"like", "dislike"}:
                continue
            await self._taste.record_interaction(
                user_id=user.id,
                title_id=title_id,
                event_type=action,
            )
            if action == "like":
                likes += 1

        if likes < 1:
            raise AppError(
                "Like at least one title so we can build your taste profile.",
                status_code=400,
                code="onboarding_insufficient",
            )

        user.onboarding_completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._recommendations.invalidate_user(user.id)
        return user
