"""Build a taste profile from per-title signals (pure — no database).

``TasteService`` calls this in production and the offline evaluation harness
calls it too, so reported metrics come from the same code that serves users.

Inputs are the *effective* signals from
``app.domain.taste_signals.effective_title_signals`` — one per title, already
collapsed to the user's current opinion and time-decayed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.domain.taste_signals import (
    EXPLAIN_ANCHOR_EVENT_TYPES,
    EXPLAIN_ANCHOR_MIN_WEIGHT,
    EffectiveSignal,
)
from app.recommendation.embeddings import blend_vectors, normalize_feature_families
from app.recommendation.explanations import build_anchor_from_title, merge_explain_memory

# Any single sparse feature is clamped to ±FEATURE_CAP before family normalisation.
FEATURE_CAP = 4.5
# Accumulated weights smaller than this are dropped as noise.
FEATURE_MIN_ABS = 0.05


@dataclass(frozen=True, slots=True)
class ProfileTitle:
    """The slice of a catalog title that profile building needs."""

    id: UUID
    name: str
    year: int | None
    feature_snapshot: Mapping[str, Any]
    embedding: Sequence[float] | None


@dataclass(slots=True)
class BuiltProfile:
    features: dict[str, float]
    vector: list[float] | None
    anchors: list[dict[str, Any]]

    def features_with_memory(self) -> dict[str, Any]:
        """Scoring features plus the explain-memory block stored on TasteProfile."""
        return merge_explain_memory(self.features, self.anchors)


def build_profile(
    signals: Iterable[EffectiveSignal],
    titles: Mapping[UUID, ProfileTitle],
    *,
    import_overlay: Mapping[str, float] | None = None,
) -> BuiltProfile:
    """Turn effective signals into sparse features, a dense vector and anchors.

    * Sparse features: each title's feature snapshot × signal weight (negative
      signals subtract), plus any imported overlay, clamped and normalised per
      feature family so one channel (e.g. keywords) cannot drown the rest.
    * Dense vector: weighted mean of **positively** rated titles only. Mixing in
      disliked titles makes the vector point "away from everything", which
      retrieves arbitrary titles; dislikes act through the sparse penalty instead.
    * Anchors: strongly liked titles, cited in "Because you liked X" reasons.
    """
    accumulated: dict[str, float] = {}
    positives: list[tuple[Sequence[float], float]] = []
    anchors: list[dict[str, Any]] = []

    # Most recent first so equally strong anchors are ordered by recency.
    for signal in sorted(signals, key=lambda s: s.created_at, reverse=True):
        title = titles.get(signal.title_id)
        if title is None:
            continue
        for key, value in title.feature_snapshot.items():
            if str(key).startswith("__"):
                continue
            try:
                contribution = float(value) * signal.weight
            except (TypeError, ValueError):
                continue
            accumulated[str(key)] = accumulated.get(str(key), 0.0) + contribution

        if signal.weight > 0 and title.embedding is not None:
            positives.append((title.embedding, signal.weight))

        if (
            signal.event_type in EXPLAIN_ANCHOR_EVENT_TYPES
            and signal.raw_weight >= EXPLAIN_ANCHOR_MIN_WEIGHT
        ):
            anchors.append(
                build_anchor_from_title(
                    title_id=title.id,
                    name=title.name,
                    event_type=signal.event_type,
                    weight=signal.raw_weight,
                    feature_snapshot=dict(title.feature_snapshot),
                    year=title.year,
                )
            )

    for key, value in (import_overlay or {}).items():
        accumulated[key] = accumulated.get(key, 0.0) + float(value)

    clamped = {
        key: max(min(round(value, 4), FEATURE_CAP), -FEATURE_CAP)
        for key, value in accumulated.items()
        if abs(value) > FEATURE_MIN_ABS
    }
    return BuiltProfile(
        features=normalize_feature_families(clamped),
        vector=blend_vectors(positives),
        anchors=anchors,
    )
