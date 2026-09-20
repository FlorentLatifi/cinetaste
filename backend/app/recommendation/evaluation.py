"""Offline evaluation of the recommender.

Protocol (leave-out, per user):

1. Split a user's rated titles into *train* (what the profile is built from)
   and *test* (held-out titles they rated positively).
2. Build the profile with the production code path — the same
   ``effective_title_signals`` → ``build_profile`` used by the API.
3. Rank the whole catalog, excluding everything in train.
4. Score the ranking against the held-out titles.

Strategies share that protocol, so the numbers compare like with like:
``hybrid`` is what production serves, the ablations isolate one channel, and
``popular`` / ``random`` are the baselines every recommender must beat.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import mean
from typing import Any
from uuid import UUID

from app.domain.taste_signals import EffectiveSignal, effective_title_signals
from app.recommendation import metrics as M
from app.recommendation.pipeline import DEFAULT_WEIGHTS, RankingWeights, primary_genre, rank_titles
from app.recommendation.profile import ProfileTitle, build_profile

DEFAULT_K = 10


@dataclass(frozen=True, slots=True)
class _Genre:
    name: str


@dataclass
class EvalTitle:
    """Catalog row shaped like the ORM model that ranking expects."""

    id: UUID
    name: str
    embedding: list[float] | None
    extra: dict[str, Any]
    popularity: float = 0.0
    vote_average: float = 0.0
    vote_count: int = 0
    year: int | None = None
    genre_names: tuple[str, ...] = ()

    @property
    def genres(self) -> list[_Genre]:
        return [_Genre(name) for name in self.genre_names]

    def as_profile_title(self) -> ProfileTitle:
        return ProfileTitle(
            id=self.id,
            name=self.name,
            year=self.year,
            feature_snapshot=(self.extra or {}).get("feature_snapshot") or {},
            embedding=self.embedding,
        )


@dataclass
class EvalUser:
    user_id: str
    # (title_id, event_type, weight, timestamp) — the shape the domain expects.
    train_events: list[tuple[UUID, str, float, datetime]]
    test_positive_ids: set[UUID]
    # Optional graded relevance (rating → gain) for NDCG.
    test_gains: dict[UUID, float] = field(default_factory=dict)


@dataclass
class Dataset:
    name: str
    titles: list[EvalTitle]
    users: list[EvalUser]

    @property
    def by_id(self) -> dict[UUID, EvalTitle]:
        return {t.id: t for t in self.titles}


Strategy = Callable[[EvalUser, Dataset, int], list[UUID]]


def _profile(user: EvalUser, dataset: Dataset) -> tuple[list[float] | None, dict[str, float]]:
    signals: Mapping[UUID, EffectiveSignal] = effective_title_signals(
        user.train_events, now=datetime.now(UTC), half_life_days=None
    )
    titles = {tid: dataset.by_id[tid].as_profile_title() for tid in signals if tid in dataset.by_id}
    built = build_profile(signals.values(), titles)
    return built.vector, built.features


def _seen(user: EvalUser) -> set[UUID]:
    return {event[0] for event in user.train_events}


def make_ranking_strategy(
    *,
    weights: RankingWeights = DEFAULT_WEIGHTS,
    mmr_lambda: float = 0.7,
    exploration_slots: int = 0,
    max_per_genre: int | None = None,
) -> Strategy:
    """The production ranker with a given configuration."""

    def strategy(user: EvalUser, dataset: Dataset, k: int) -> list[UUID]:
        vector, features = _profile(user, dataset)
        ranked = rank_titles(
            user_vector=vector,
            user_features=features,
            titles=dataset.titles,
            exclude_ids=_seen(user),
            slate_size=k,
            mmr_lambda=mmr_lambda,
            exploration_slots=exploration_slots,
            max_per_genre=max_per_genre,
            weights=weights,
            with_reasons=False,
        )
        return [item.title_id for item in ranked]

    return strategy


def popularity_strategy(user: EvalUser, dataset: Dataset, k: int) -> list[UUID]:
    seen = _seen(user)
    ordered = sorted(dataset.titles, key=lambda t: t.popularity, reverse=True)
    return [t.id for t in ordered if t.id not in seen][:k]


def random_strategy(user: EvalUser, dataset: Dataset, k: int) -> list[UUID]:
    seen = _seen(user)
    pool = [t.id for t in dataset.titles if t.id not in seen]
    rng = random.Random(hash(user.user_id) & 0xFFFF)
    rng.shuffle(pool)
    return pool[:k]


DEFAULT_STRATEGIES: dict[str, Strategy] = {
    "random": random_strategy,
    "popular": popularity_strategy,
    "dense_only": make_ranking_strategy(
        weights=RankingWeights(similarity=1.0, sparse_positive=0.0, sparse_negative=0.0),
        mmr_lambda=1.0,
        max_per_genre=999,
    ),
    "sparse_only": make_ranking_strategy(
        weights=RankingWeights(similarity=0.0, sparse_positive=1.0, sparse_negative=0.3),
        mmr_lambda=1.0,
        max_per_genre=999,
    ),
    "hybrid_no_diversity": make_ranking_strategy(mmr_lambda=1.0, max_per_genre=999),
    "hybrid": make_ranking_strategy(mmr_lambda=0.8),
    "hybrid_with_exploration": make_ranking_strategy(mmr_lambda=0.8, exploration_slots=3),
}


def evaluate(
    dataset: Dataset,
    strategies: Mapping[str, Strategy] | None = None,
    *,
    k: int = DEFAULT_K,
) -> dict[str, dict[str, float]]:
    """Run every strategy over every user; return metric → value per strategy."""
    strategies = strategies or DEFAULT_STRATEGIES
    catalog = dataset.by_id
    popularity_rank = _popularity_percentiles(dataset.titles)
    results: dict[str, dict[str, float]] = {}

    for name, strategy in strategies.items():
        per_user: list[dict[str, float]] = []
        slates: list[Sequence[UUID]] = []
        for user in dataset.users:
            if not user.test_positive_ids:
                continue
            recommended = strategy(user, dataset, k)
            slates.append(recommended)
            gains = user.test_gains or {tid: 1.0 for tid in user.test_positive_ids}
            vectors = [
                catalog[tid].embedding
                for tid in recommended
                if tid in catalog and catalog[tid].embedding is not None
            ]
            genres = [primary_genre(catalog[tid]) for tid in recommended if tid in catalog]
            per_user.append(
                {
                    f"recall@{k}": M.recall_at_k(recommended, user.test_positive_ids, k=k),
                    f"precision@{k}": M.precision_at_k(recommended, user.test_positive_ids, k=k),
                    f"ndcg@{k}": M.ndcg_at_k(recommended, gains, k=k),
                    f"hit_rate@{k}": M.hit_rate_at_k(recommended, user.test_positive_ids, k=k),
                    "mrr": M.reciprocal_rank(recommended, user.test_positive_ids),
                    "diversity": M.intra_list_diversity(vectors),
                    "genre_variety": M.distinct_ratio(genres),
                    "novelty": M.novelty(recommended, popularity_rank),
                }
            )
        if not per_user:
            continue
        summary = {metric: mean(row[metric] for row in per_user) for metric in per_user[0]}
        summary["coverage"] = M.catalog_coverage(slates, len(dataset.titles))
        summary["users"] = float(len(per_user))
        results[name] = summary
    return results


def _popularity_percentiles(titles: Sequence[EvalTitle]) -> dict[UUID, float]:
    """0.0 for the most popular title, 1.0 for the least."""
    if not titles:
        return {}
    ordered = sorted(titles, key=lambda t: t.popularity, reverse=True)
    last = max(len(ordered) - 1, 1)
    return {title.id: index / last for index, title in enumerate(ordered)}


def format_report(results: Mapping[str, Mapping[str, float]], *, k: int = DEFAULT_K) -> str:
    """Markdown table, best strategy per metric is easy to eyeball."""
    if not results:
        return "(no results)"
    columns = [
        f"recall@{k}",
        f"ndcg@{k}",
        f"hit_rate@{k}",
        "mrr",
        "diversity",
        "genre_variety",
        "novelty",
        "coverage",
    ]
    lines = [
        "| strategy | " + " | ".join(columns) + " |",
        "|" + "---|" * (len(columns) + 1),
    ]
    for name, row in results.items():
        cells = [f"{row.get(column, float('nan')):.3f}" for column in columns]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
