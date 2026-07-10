from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.models.interaction import InteractionEvent, UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.recommendation.embeddings import blend_vectors

# Signal weights for taste learning
SIGNAL_WEIGHTS = {
    "like": 1.0,
    "dislike": -0.85,
    "watchlist": 0.45,
    "not_interested": -1.0,
    "skip": -0.15,
    "view": 0.05,
}

STATE_FROM_EVENT = {
    "like": "like",
    "dislike": "dislike",
    "watchlist": "watchlist",
    "not_interested": "not_interested",
}


class TasteService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_interaction(
        self,
        *,
        user_id: UUID,
        title_id: UUID,
        event_type: str,
        weight: float | None = None,
    ) -> UserTitleState:
        if event_type not in SIGNAL_WEIGHTS:
            raise ValueError(f"Unsupported event_type: {event_type}")

        event_weight = SIGNAL_WEIGHTS[event_type] if weight is None else weight
        self._session.add(
            InteractionEvent(
                user_id=user_id,
                title_id=title_id,
                event_type=event_type,
                weight=event_weight,
            )
        )

        state = await self._session.scalar(
            select(UserTitleState).where(
                UserTitleState.user_id == user_id,
                UserTitleState.title_id == title_id,
            )
        )
        new_state = STATE_FROM_EVENT.get(event_type)
        if new_state:
            if state is None:
                state = UserTitleState(user_id=user_id, title_id=title_id, state=new_state)
                self._session.add(state)
            else:
                state.state = new_state
        elif state is None:
            state = UserTitleState(user_id=user_id, title_id=title_id, state="none")
            self._session.add(state)

        await self._session.flush()
        await self.recompute_profile(user_id)
        return state

    async def recompute_profile(self, user_id: UUID) -> TasteProfile:
        events = (
            await self._session.scalars(
                select(InteractionEvent)
                .where(InteractionEvent.user_id == user_id)
                .order_by(InteractionEvent.created_at.asc())
            )
        ).all()

        title_ids = {e.title_id for e in events}
        titles: dict[UUID, Title] = {}
        if title_ids:
            rows = (
                await self._session.scalars(
                    select(Title)
                    .where(Title.id.in_(title_ids))
                    .options(selectinload(Title.genres), selectinload(Title.keywords))
                )
            ).all()
            titles = {t.id: t for t in rows}

        feature_acc: dict[str, float] = {}
        vectors: list[tuple[list[float], float]] = []

        for event in events:
            title = titles.get(event.title_id)
            if title is None:
                continue
            snap = (title.extra or {}).get("feature_snapshot") or {}
            for key, value in snap.items():
                feature_acc[key] = feature_acc.get(key, 0.0) + float(value) * event.weight
            if title.embedding is not None:  # numpy/pgvector: use identity check only
                vectors.append((list(title.embedding), float(event.weight)))

        # Drop near-zero noise
        features = {k: round(v, 4) for k, v in feature_acc.items() if abs(v) > 0.05}
        vector = blend_vectors(vectors)

        profile = await self._session.get(TasteProfile, user_id)
        if profile is None:
            profile = TasteProfile(user_id=user_id, version=1, features=features, vector=vector)
            self._session.add(profile)
        else:
            profile.features = features
            profile.vector = vector
            profile.version = int(profile.version or 1) + 1

        await self._session.flush()
        return profile

    async def get_profile(self, user_id: UUID) -> TasteProfile | None:
        return await self._session.get(TasteProfile, user_id)

    def top_positive_features(self, features: dict[str, Any], limit: int = 12) -> list[tuple[str, float]]:
        positives = [(k, float(v)) for k, v in features.items() if float(v) > 0]
        positives.sort(key=lambda x: x[1], reverse=True)
        return positives[:limit]
