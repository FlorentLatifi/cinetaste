"""Taste profile learning from user–title interactions.

Signal weights, polarities, tiers, feed exclusion and special cases are defined
once in ``app.domain.taste_signals`` (see also ``docs/TASTE_SIGNALS.md``).

This service:
1. Records append-only InteractionEvents with policy weights
2. Updates UserTitleState for feed/watchlist filters
3. Recomputes the profile: events → one effective signal per title (latest
   opinion wins, time-decayed) → sparse features + dense vector + anchors

Performance note
----------------
A recompute reads the user's event log (small rows, indexed by user and time)
and only the titles that still carry a signal, as plain columns. That is
O(interactions) per recompute — fine up to thousands of interactions per user.
Bulk flows (onboarding complete) use ``recompute=False`` and recompute once.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.taste_summary import IMPORT_OVERLAY_KEY, merge_import_overlay
from app.core.config import get_settings
from app.domain.taste_signals import (
    EXPLAIN_ANCHOR_EVENT_TYPES,
    EXPLAIN_ANCHOR_MIN_WEIGHT,
    FEED_EXCLUDE_STATES,
    POSITIVE_RATING_EVENT_TYPES,
    RATING_EVENT_TYPES,
    SIGNAL_POLICIES,
    SIGNAL_WEIGHTS,
    STATE_FROM_EVENT,
    ZERO_SIGNAL_EPS,
    affects_taste,
    effective_title_signals,
    get_policy,
    is_supported_event,
    weight_for,
)
from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.models.interaction import InteractionEvent, UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.recommendation.profile import ProfileTitle, build_profile

# Re-export policy symbols so existing imports keep working:
#   from app.application.taste_service import SIGNAL_WEIGHTS, FEED_EXCLUDE_STATES, ...
__all__ = [
    "TasteService",
    "SIGNAL_WEIGHTS",
    "SIGNAL_POLICIES",
    "STATE_FROM_EVENT",
    "FEED_EXCLUDE_STATES",
    "RATING_EVENT_TYPES",
    "POSITIVE_RATING_EVENT_TYPES",
    "EXPLAIN_ANCHOR_EVENT_TYPES",
    "ZERO_SIGNAL_EPS",
    "EXPLAIN_ANCHOR_MIN_WEIGHT",
    "get_policy",
    "weight_for",
    "affects_taste",
]


def _read_import_overlay(profile: TasteProfile | None) -> dict[str, float]:
    overlay: dict[str, float] = {}
    if profile is None or not isinstance(profile.features, dict):
        return overlay
    raw = profile.features.get(IMPORT_OVERLAY_KEY)
    if not isinstance(raw, dict):
        return overlay
    for key, value in raw.items():
        if str(key).startswith("__"):
            continue
        try:
            overlay[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return overlay


class TasteService:
    def __init__(self, session: AsyncSession, *, half_life_days: float | None = None) -> None:
        self._session = session
        self._half_life_days = (
            get_settings().taste_half_life_days if half_life_days is None else half_life_days
        )

    async def record_interaction(
        self,
        *,
        user_id: UUID,
        title_id: UUID,
        event_type: str,
        weight: float | None = None,
        recompute: bool = True,
    ) -> UserTitleState:
        """Persist one interaction.

        Set ``recompute=False`` when recording many events in a loop, then call
        ``recompute_profile`` once.
        """
        if not is_supported_event(event_type):
            raise ValueError(f"Unsupported event_type: {event_type}")

        policy = get_policy(event_type)
        event_weight = policy.weight if weight is None else weight
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
        new_state = policy.user_title_state
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
        if recompute:
            await self.recompute_profile(user_id)
        return state

    async def recompute_profile(self, user_id: UUID) -> TasteProfile:
        events = (
            await self._session.execute(
                select(
                    InteractionEvent.title_id,
                    InteractionEvent.event_type,
                    InteractionEvent.weight,
                    InteractionEvent.created_at,
                ).where(InteractionEvent.user_id == user_id)
            )
        ).all()
        signals = effective_title_signals(
            ((e.title_id, e.event_type, float(e.weight), e.created_at) for e in events),
            now=datetime.now(UTC),
            half_life_days=self._half_life_days,
        )

        titles: dict[UUID, ProfileTitle] = {}
        if signals:
            rows = (
                await self._session.execute(
                    select(
                        Title.id, Title.name, Title.release_date, Title.extra, Title.embedding
                    ).where(Title.id.in_(list(signals)))
                )
            ).all()
            titles = {
                row.id: ProfileTitle(
                    id=row.id,
                    name=row.name,
                    year=row.release_date.year if row.release_date else None,
                    feature_snapshot=(row.extra or {}).get("feature_snapshot") or {},
                    embedding=row.embedding,
                )
                for row in rows
            }

        # The import overlay is durable: it survives recomputes until cleared.
        profile = await self._session.get(TasteProfile, user_id)
        import_overlay = _read_import_overlay(profile)
        built = build_profile(signals.values(), titles, import_overlay=import_overlay)

        features: dict[str, Any] = built.features_with_memory()
        if import_overlay:
            features[IMPORT_OVERLAY_KEY] = {k: round(v, 4) for k, v in import_overlay.items()}

        if profile is None:
            profile = TasteProfile(user_id=user_id, version=1, features=features, vector=built.vector)
            self._session.add(profile)
        else:
            profile.features = features
            profile.vector = built.vector
            profile.version = int(profile.version or 1) + 1

        await self._session.flush()
        return profile

    async def merge_taste_snapshot(
        self,
        user_id: UUID,
        *,
        likes: list[dict[str, Any]],
        dislikes: list[dict[str, Any]],
    ) -> TasteProfile:
        """Merge an exported snapshot into a durable import overlay, then recompute."""
        profile = await self._session.get(TasteProfile, user_id)
        existing_features: dict[str, Any] = {}
        if profile is not None and isinstance(profile.features, dict):
            existing_features = dict(profile.features)

        raw_overlay = existing_features.get(IMPORT_OVERLAY_KEY)
        overlay = merge_import_overlay(
            raw_overlay if isinstance(raw_overlay, dict) else None,
            likes=likes,
            dislikes=dislikes,
        )
        if not overlay and not likes and not dislikes:
            # Nothing to merge — still return profile
            if profile is None:
                profile = TasteProfile(
                    user_id=user_id, version=1, features={}, vector=None
                )
                self._session.add(profile)
                await self._session.flush()
            return profile

        existing_features[IMPORT_OVERLAY_KEY] = overlay
        if profile is None:
            profile = TasteProfile(
                user_id=user_id,
                version=1,
                features=existing_features,
                vector=None,
            )
            self._session.add(profile)
        else:
            profile.features = existing_features
        await self._session.flush()
        return await self.recompute_profile(user_id)

    async def clear_import_overlay(self, user_id: UUID) -> TasteProfile:
        """Remove durable snapshot overlay and recompute from live events only."""
        profile = await self._session.get(TasteProfile, user_id)
        if profile is None:
            profile = TasteProfile(user_id=user_id, version=1, features={}, vector=None)
            self._session.add(profile)
            await self._session.flush()
            return profile

        features = dict(profile.features or {})
        if IMPORT_OVERLAY_KEY in features:
            features.pop(IMPORT_OVERLAY_KEY, None)
            profile.features = features
            await self._session.flush()
        return await self.recompute_profile(user_id)

    async def get_profile(self, user_id: UUID) -> TasteProfile | None:
        return await self._session.get(TasteProfile, user_id)

    def top_positive_features(self, features: dict[str, Any], limit: int = 12) -> list[tuple[str, float]]:
        positives: list[tuple[str, float]] = []
        for k, v in features.items():
            if str(k).startswith("__"):
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                positives.append((str(k), fv))
        positives.sort(key=lambda x: x[1], reverse=True)
        return positives[:limit]
