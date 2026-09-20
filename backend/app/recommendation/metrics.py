"""Ranking metrics for offline evaluation.

Accuracy metrics say whether the titles a user actually liked were ranked
highly. Beyond-accuracy metrics say whether the slate is worth looking at: a
recommender that only returns the ten most popular titles scores reasonably on
recall and terribly on novelty and coverage.

All functions take an ordered list of recommended ids (best first).
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import Any

Id = Hashable


def precision_at_k(recommended: Sequence[Id], relevant: set[Id], *, k: int) -> float:
    if k <= 0:
        return 0.0
    head = recommended[:k]
    if not head:
        return 0.0
    return sum(1 for item in head if item in relevant) / float(k)


def recall_at_k(recommended: Sequence[Id], relevant: set[Id], *, k: int) -> float:
    if not relevant:
        return 0.0
    head = set(recommended[:k])
    return len(head & relevant) / float(len(relevant))


def hit_rate_at_k(recommended: Sequence[Id], relevant: set[Id], *, k: int) -> float:
    """1.0 when at least one relevant title made the top K."""
    if not relevant or k <= 0:
        return 0.0
    return 1.0 if set(recommended[:k]) & relevant else 0.0


def reciprocal_rank(recommended: Sequence[Id], relevant: set[Id]) -> float:
    for position, item in enumerate(recommended, start=1):
        if item in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(
    recommended: Sequence[Id],
    relevant: set[Id] | Mapping[Id, float],
    *,
    k: int,
) -> float:
    """Normalised discounted cumulative gain.

    Rewards putting relevant titles near the top: a hit at rank 1 is worth
    1.0, at rank 5 about 0.43. Accepts graded relevance (id → gain).
    """
    if k <= 0 or not relevant:
        return 0.0
    gains: Mapping[Id, float] = (
        {item: 1.0 for item in relevant} if isinstance(relevant, set) else relevant
    )
    dcg = sum(
        gains.get(item, 0.0) / math.log2(position + 1)
        for position, item in enumerate(recommended[:k], start=1)
    )
    ideal = sorted(gains.values(), reverse=True)[:k]
    idcg = sum(gain / math.log2(position + 1) for position, gain in enumerate(ideal, start=1))
    return dcg / idcg if idcg > 0 else 0.0


def intra_list_diversity(vectors: Sequence[Sequence[float]]) -> float:
    """1 − mean pairwise cosine of the slate (higher = less repetitive)."""
    usable = [v for v in vectors if v is not None and len(v) > 0]
    if len(usable) < 2:
        return 0.0
    import numpy as np

    matrix = np.asarray(usable, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, norms, out=matrix, where=norms > 1e-12)
    similarity = matrix @ matrix.T
    n = len(usable)
    mean_pairwise = (similarity.sum() - np.trace(similarity)) / (n * (n - 1))
    return float(1.0 - mean_pairwise)


def distinct_ratio(values: Iterable[Any]) -> float:
    """Share of distinct values, e.g. genres in one slate."""
    items = list(values)
    return len(set(items)) / float(len(items)) if items else 0.0


def novelty(recommended: Sequence[Id], popularity_rank: Mapping[Id, float]) -> float:
    """Mean popularity percentile of the slate, 0 (blockbusters) … 1 (obscure)."""
    scores = [popularity_rank[item] for item in recommended if item in popularity_rank]
    return sum(scores) / len(scores) if scores else 0.0


def catalog_coverage(slates: Iterable[Sequence[Id]], catalog_size: int) -> float:
    """Share of the catalog that appears in at least one slate."""
    if catalog_size <= 0:
        return 0.0
    shown: set[Id] = set()
    for slate in slates:
        shown.update(slate)
    return len(shown) / float(catalog_size)
