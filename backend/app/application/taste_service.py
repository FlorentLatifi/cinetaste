from __future__ import annotations

"""Taste profile learning from user–title interactions.

Signal semantics (how each action moves genre weights / embeddings)
------------------------------------------------------------------
Every stored InteractionEvent has a numeric ``weight``. On recompute we
accumulate ``feature_snapshot[key] * weight`` into sparse genre/keyword
features and blend title embeddings with the same weight.

| event_type       | weight | Affects taste? | UserTitleState   | Notes |
|------------------|--------|----------------|------------------|-------|
| haven't_seen     |  0.0   | **No**         | haven't_seen     | Seen in onboarding only for UX/analytics. Zero contribution to features/vector. Not excluded from For You. |
| not_interested   | -0.40  | Mild negative  | not_interested   | User knows the title (or type) and rejects it. Mild genre push-away; excluded from For You. |
| rate_1 (Bad)     | -0.90  | Strong negative| dislike          | Explicit low rating after claiming familiarity. |
| rate_2 (It's ok) |  0.30  | Weak positive  | rated            | Soft positive — seen but not loved. |
| rate_3 (Good)    |  1.00  | Positive       | like             | Solid like. |
| rate_4 (Favorite)|  1.55  | Strong positive| like             | Boosts matching genres/embeddings hardest. |
| like             |  1.00  | Positive       | like             | Post-onboarding shortcut (same as rate_3). |
| dislike          | -0.85  | Strong negative| dislike          | Post-onboarding shortcut (≈ rate_1). |
| watchlist        |  0.45  | Mild positive  | watchlist        | Intent to watch. |
| skip             | -0.15  | Tiny negative  | (none)           | Soft pass in feed; barely moves profile. |
| view             |  0.05  | Near-zero      | (none)           | Impression logging; almost no learning. |

Events with ``abs(weight) < ZERO_SIGNAL_EPS`` are skipped entirely during
recompute so they cannot drift genre weights or embeddings.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.models.interaction import InteractionEvent, UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.recommendation.embeddings import blend_vectors, normalize_feature_families
from app.recommendation.explanations import (
    build_anchor_from_title,
    merge_explain_memory,
)

# Below this absolute weight, an event never touches features or embeddings.
ZERO_SIGNAL_EPS = 1e-9

# Signal weights for taste learning (see module docstring).
SIGNAL_WEIGHTS: dict[str, float] = {
    "haven't_seen": 0.0,
    "not_interested": -0.40,
    "rate_1": -0.90,
    "rate_2": 0.30,
    "rate_3": 1.00,
    "rate_4": 1.55,
    "like": 1.0,
    "dislike": -0.85,
    "watchlist": 0.45,
    "skip": -0.15,
    "view": 0.05,
}

# Events that count as an explicit rating of a title the user has seen.
RATING_EVENT_TYPES = frozenset({"rate_1", "rate_2", "rate_3", "rate_4"})

# Positive rating steps (used for onboarding quality gates).
POSITIVE_RATING_EVENT_TYPES = frozenset({"rate_2", "rate_3", "rate_4", "like"})

STATE_FROM_EVENT: dict[str, str] = {
    "like": "like",
    "dislike": "dislike",
    "watchlist": "watchlist",
    "not_interested": "not_interested",
    "haven't_seen": "haven't_seen",
    "rate_1": "dislike",
    "rate_2": "rated",
    "rate_3": "like",
    "rate_4": "like",
}

# States that should not reappear on the For You slate.
FEED_EXCLUDE_STATES = frozenset(
    {"like", "dislike", "not_interested", "watchlist", "rated"}
)
# "haven't_seen" is intentionally NOT excluded — user may still enjoy a rec.


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
        # Zero-signal events still recompute (no-op for that event) so profile
        # version stays consistent if other events exist.
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
        # Strongest positive ratings become citation anchors for "because you liked X".
        anchors: list[dict[str, Any]] = []
        # Latest event per title wins for anchor metadata.
        latest_positive: dict[UUID, Any] = {}

        for event in events:
            # Haven't-seen and any other zero-weight event: no taste influence.
            if abs(float(event.weight)) < ZERO_SIGNAL_EPS:
                continue
            if event.event_type == "haven't_seen":
                continue

            title = titles.get(event.title_id)
            if title is None:
                continue
            snap = (title.extra or {}).get("feature_snapshot") or {}
            for key, value in snap.items():
                if str(key).startswith("__"):
                    continue
                try:
                    feature_acc[str(key)] = feature_acc.get(str(key), 0.0) + float(value) * float(
                        event.weight
                    )
                except (TypeError, ValueError):
                    continue
            if title.embedding is not None:  # numpy/pgvector: use identity check only
                vectors.append((list(title.embedding), float(event.weight)))

            if float(event.weight) >= 0.85 and event.event_type in POSITIVE_RATING_EVENT_TYPES:
                latest_positive[event.title_id] = event

        for title_id, event in latest_positive.items():
            title = titles.get(title_id)
            if title is None:
                continue
            year = title.release_date.year if title.release_date else None
            anchors.append(
                build_anchor_from_title(
                    title_id=title.id,
                    name=title.name,
                    event_type=event.event_type,
                    weight=float(event.weight),
                    feature_snapshot=(title.extra or {}).get("feature_snapshot") or {},
                    year=year,
                )
            )

        # Drop near-zero noise, then rebalance families so keywords don't drown directors.
        raw_features = {k: round(v, 4) for k, v in feature_acc.items() if abs(v) > 0.05}
        # Soft per-key cap before family norms (limits single-title domination).
        capped = {k: max(min(v, 4.5), -4.5) for k, v in raw_features.items()}
        features = normalize_feature_families(capped)
        # Persist named favorites + creative DNA for human explanations.
        features_with_memory = merge_explain_memory(features, anchors)
        vector = blend_vectors(vectors)

        profile = await self._session.get(TasteProfile, user_id)
        if profile is None:
            profile = TasteProfile(
                user_id=user_id, version=1, features=features_with_memory, vector=vector
            )
            self._session.add(profile)
        else:
            profile.features = features_with_memory
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
