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

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


# Namespace for the per-user advisory lock that serialises profile rebuilds,
# so it cannot collide with any other advisory lock the app may take later.
_RECOMPUTE_LOCK_NAMESPACE = 0x7A57E


def _recompute_lock_key(user_id: UUID) -> int:
    """A signed 64-bit advisory-lock key for one user's profile rebuilds.

    Two users sharing a key (a 2^-64 event) would only make their rebuilds
    wait for each other — never change a result.
    """
    raw = int.from_bytes(user_id.bytes[:8], "big", signed=True)
    return raw ^ _RECOMPUTE_LOCK_NAMESPACE


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


async def load_profile_titles(
    session: AsyncSession, title_ids: list[UUID]
) -> dict[UUID, ProfileTitle]:
    """The slice of each title that profile building needs, as plain columns.

    Shared by the stored profile and the guest profile, which is built the same
    way but never written anywhere.
    """
    if not title_ids:
        return {}
    rows = (
        await session.execute(
            select(Title.id, Title.name, Title.release_date, Title.extra, Title.embedding).where(
                Title.id.in_(title_ids)
            )
        )
    ).all()
    return {
        row.id: ProfileTitle(
            id=row.id,
            name=row.name,
            year=row.release_date.year if row.release_date else None,
            feature_snapshot=(row.extra or {}).get("feature_snapshot") or {},
            embedding=row.embedding,
        )
        for row in rows
    }


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

    async def bump_profile_version(self, user_id: UUID) -> int:
        """Mark the profile as changed without rebuilding it.

        The For You cache is keyed by this version, so bumping it is what makes
        a rating visible — the slate is rebuilt on the next request. Rebuilding
        the profile itself is the expensive half and can follow behind; see
        ``application.taste_recompute``.

        One atomic upsert (``version = version + 1`` in SQL): it creates the row
        on a user's first rating, and two ratings landing at once both count. A
        read-modify-write in Python let two requests read the same version and
        write the same next one, so the second rating's slate could be served
        from the first one's cache entry.
        """
        stmt = pg_insert(TasteProfile).values(
            user_id=user_id, version=1, features={}, vector=None
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TasteProfile.user_id],
            set_={"version": TasteProfile.version + 1, "updated_at": func.now()},
        ).returning(TasteProfile.version)
        version = int((await self._session.execute(stmt)).scalar_one())
        self._forget_loaded_profile(user_id)
        return version

    async def _lock_profile_rebuilds(self, user_id: UUID) -> None:
        """Serialise every profile rebuild for this user until the transaction ends.

        Ratings are rebuilt after the response, so two quick ratings start two
        rebuilds. Without this, the one that read the event log first could
        commit last and overwrite the newer profile — the latest rating then
        silently missing from recommendations until the next one. With it, the
        second rebuild waits, then reads a log that includes both ratings.

        A transaction-scoped advisory lock rather than ``SELECT … FOR UPDATE``
        on the profile row: the row may not exist yet, and the request path's
        version bump must never wait for a rebuild to finish.
        """
        await self._session.execute(
            select(func.pg_advisory_xact_lock(_recompute_lock_key(user_id)))
        )

    def _forget_loaded_profile(self, user_id: UUID) -> None:
        """Drop a stale in-session copy after the row changed in SQL."""
        for obj in list(self._session.identity_map.values()):
            if isinstance(obj, TasteProfile) and obj.user_id == user_id:
                self._session.expire(obj)

    async def recompute_profile(self, user_id: UUID) -> TasteProfile:
        await self._lock_profile_rebuilds(user_id)
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

        titles = await load_profile_titles(self._session, list(signals))

        # The import overlay is durable: it survives recomputes until cleared.
        # Read after taking the lock, and past any copy this session already
        # holds, so an overlay written by a concurrent import is not lost.
        await self._session.flush()
        profile = await self._session.get(TasteProfile, user_id, populate_existing=True)
        import_overlay = _read_import_overlay(profile)
        built = build_profile(signals.values(), titles, import_overlay=import_overlay)

        features: dict[str, Any] = built.features_with_memory()
        if import_overlay:
            features[IMPORT_OVERLAY_KEY] = {k: round(v, 4) for k, v in import_overlay.items()}

        # Upsert with the version incremented in SQL, so a bump that landed
        # while this rebuild was running is kept rather than overwritten.
        stmt = pg_insert(TasteProfile).values(
            user_id=user_id, version=1, features=features, vector=built.vector
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TasteProfile.user_id],
            set_={
                "features": stmt.excluded.features,
                "vector": stmt.excluded.vector,
                "version": TasteProfile.version + 1,
                "updated_at": func.now(),
            },
        ).returning(TasteProfile)
        result = await self._session.execute(
            stmt, execution_options={"populate_existing": True}
        )
        return result.scalar_one()

    async def merge_taste_snapshot(
        self,
        user_id: UUID,
        *,
        likes: list[dict[str, Any]],
        dislikes: list[dict[str, Any]],
    ) -> TasteProfile:
        """Merge an exported snapshot into a durable import overlay, then recompute."""
        # Taken before this transaction touches the profile row; recompute
        # takes it again (advisory locks nest). The other order — row first,
        # lock second — deadlocks against a background rebuild that holds the
        # lock and is waiting for the row.
        await self._lock_profile_rebuilds(user_id)
        profile = await self._session.get(TasteProfile, user_id, populate_existing=True)
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
        await self._lock_profile_rebuilds(user_id)  # before the row; see merge_taste_snapshot
        profile = await self._session.get(TasteProfile, user_id, populate_existing=True)
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
