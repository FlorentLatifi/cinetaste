"""Lightweight offline evaluation helpers for ranking quality.

Designed for unit tests and CLI scripts — no database required when you pass
in-memory title objects and synthetic “held-out” likes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.recommendation.pipeline import rank_titles


def precision_at_k(recommended_ids: list[UUID], relevant_ids: set[UUID], *, k: int) -> float:
    if k <= 0:
        return 0.0
    head = recommended_ids[:k]
    if not head:
        return 0.0
    hits = sum(1 for tid in head if tid in relevant_ids)
    return hits / float(k)


def hit_rate_at_k(recommended_ids: list[UUID], relevant_ids: set[UUID], *, k: int) -> float:
    if k <= 0 or not relevant_ids:
        return 0.0
    head = set(recommended_ids[:k])
    return 1.0 if head & relevant_ids else 0.0


def evaluate_held_out_like(
    *,
    user_vector: list[float] | None,
    user_features: dict[str, Any],
    catalog: list[Any],
    held_out_id: UUID,
    exclude_ids: set[UUID] | None = None,
    slate_size: int = 20,
    k: int = 10,
    mmr_lambda: float = 0.7,
) -> dict[str, float]:
    """Rank with held-out title still in the catalog; measure whether it surfaces.

    Typical protocol: build profile from all likes except one, rank full catalog,
    check if the held-out like appears in top-K.
    """
    exclude = set(exclude_ids or set())
    ranked = rank_titles(
        user_vector=user_vector,
        user_features=user_features,
        titles=catalog,
        exclude_ids=exclude,
        slate_size=slate_size,
        mmr_lambda=mmr_lambda,
        exploration_slots=0,
    )
    ids = [item.title_id for item in ranked]
    relevant = {held_out_id}
    return {
        "precision_at_k": precision_at_k(ids, relevant, k=k),
        "hit_rate_at_k": hit_rate_at_k(ids, relevant, k=k),
        "rank": float(ids.index(held_out_id) + 1) if held_out_id in ids else -1.0,
        "slate_size": float(len(ids)),
    }
