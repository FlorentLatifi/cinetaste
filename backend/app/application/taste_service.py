from __future__ import annotations

"""Taste profile learning from user–title interactions.

Signal weights, polarities, feed exclusion, and special cases are defined once in
``app.domain.taste_signals`` (see also ``docs/TASTE_SIGNALS.md``).

This service:
1. Records append-only InteractionEvents with policy weights
2. Updates UserTitleState for feed/watchlist filters
3. Recomputes sparse features + dense vector + explain anchors

Performance note
----------------
``record_interaction(..., recompute=True)`` is the default for single feed actions.
Bulk flows (onboarding complete) MUST use ``recompute=False`` and call
``recompute_profile`` once at the end to avoid O(n²) full-history replays.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    get_policy,
    is_supported_event,
    is_superseded_by_clear,
    last_clear_timestamps,
    weight_for,
)
from app.infrastructure.db.models.catalog import Title
from app.infrastructure.db.models.interaction import InteractionEvent, UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.application.taste_summary import IMPORT_OVERLAY_KEY, merge_import_overlay
from app.recommendation.embeddings import blend_vectors, normalize_feature_families
from app.recommendation.explanations import (
    build_anchor_from_title,
    merge_explain_memory,
)

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
        # Strongest positive ratings become citation anchors for explanations.
        latest_anchor: dict[UUID, Any] = {}

        # Append-only undo: events at or before the latest ``clear`` for a title
        # no longer contribute to sparse features, embeddings, or anchors.
        last_clear = last_clear_timestamps(
            (e.title_id, e.event_type, e.created_at) for e in events
        )

        # Preserve durable import overlay across recompute.
        existing_profile = await self._session.get(TasteProfile, user_id)
        import_overlay: dict[str, float] = {}
        if existing_profile and isinstance(existing_profile.features, dict):
            raw_overlay = existing_profile.features.get(IMPORT_OVERLAY_KEY)
            if isinstance(raw_overlay, dict):
                for k, v in raw_overlay.items():
                    if str(k).startswith("__"):
                        continue
                    try:
                        import_overlay[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue

        for event in events:
            if is_superseded_by_clear(
                title_id=event.title_id,
                event_type=event.event_type,
                created_at=event.created_at,
                last_clear=last_clear,
            ):
                continue

            w = float(event.weight)
            # Policy-driven: haven't_seen and near-zero weights never move taste.
            if not affects_taste(event.event_type, w):
                continue

            title = titles.get(event.title_id)
            if title is None:
                continue
            snap = (title.extra or {}).get("feature_snapshot") or {}
            for key, value in snap.items():
                if str(key).startswith("__"):
                    continue
                try:
                    feature_acc[str(key)] = feature_acc.get(str(key), 0.0) + float(value) * w
                except (TypeError, ValueError):
                    continue
            if title.embedding is not None:  # numpy/pgvector: identity check only
                vectors.append((list(title.embedding), w))

            if (
                event.event_type in EXPLAIN_ANCHOR_EVENT_TYPES
                and w >= EXPLAIN_ANCHOR_MIN_WEIGHT
            ):
                latest_anchor[event.title_id] = event

        # Apply import overlay into scoring features (live ratings still dominate scale).
        for key, value in import_overlay.items():
            feature_acc[key] = feature_acc.get(key, 0.0) + float(value)

        anchors: list[dict[str, Any]] = []
        for title_id, event in latest_anchor.items():
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

        raw_features = {k: round(v, 4) for k, v in feature_acc.items() if abs(v) > 0.05}
        capped = {k: max(min(v, 4.5), -4.5) for k, v in raw_features.items()}
        features = normalize_feature_families(capped)
        features_with_memory = merge_explain_memory(features, anchors)
        if import_overlay:
            features_with_memory[IMPORT_OVERLAY_KEY] = {
                k: round(v, 4) for k, v in import_overlay.items()
            }
        vector = blend_vectors(vectors)

        profile = existing_profile
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
