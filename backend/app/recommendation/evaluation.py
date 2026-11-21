"""Offline evaluation of the recommender.

Protocol (leave-out, per user):

1. Split a user's rated titles into *train* (what the profile is built from)
   and *test* (held-out titles they rated positively — and, where the dataset
   has them, held-out titles they disliked).
2. Build the profile with the production code path — the same
   ``effective_title_signals`` → ``build_profile`` used by the API.
3. Rank the catalog (or, for ``candidate_pool="production"``, the candidate
   pool production would retrieve), excluding everything in train.
4. Score the slate against the held-out titles.

Strategies share that protocol, so the numbers compare like with like:
``production`` is what the API serves, the ablations isolate one channel, and
``popular`` / ``random`` are the baselines every recommender must beat.

Users are the outer loop: a user's profile and per-candidate channels
(``candidate_channels``) are computed once and reused by every strategy, so
weight sweeps cost a blend and a slate selection per configuration, not a full
re-scoring.
"""

from __future__ import annotations

import random
import zlib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import cached_property
from statistics import mean
from typing import Any
from uuid import UUID

import numpy as np

from app.domain.taste_signals import EffectiveSignal, effective_title_signals
from app.recommendation import metrics as M
from app.recommendation.pipeline import (
    DEFAULT_WEIGHTS,
    CandidateChannels,
    RankedItem,
    RankingWeights,
    blend_scores,
    candidate_channels,
    primary_genre,
    select_slate,
)
from app.recommendation.profile import ProfileTitle, build_profile

DEFAULT_K = 10

# Production candidate generation (app/core/config.py, recommendation_service):
# ANN neighbours of the taste vector, over-fetched by the number of titles the
# user has already rated (capped), plus a slice of the most popular titles.
PROD_ANN_CANDIDATES = 250
PROD_POPULAR_CANDIDATES = 120
PROD_ANN_OVERFETCH_MAX = 150
PROD_COLD_POOL = 400
# What production serves (Settings.rec_mmr_lambda / rec_exploration_slots).
PROD_MMR_LAMBDA = 0.8
PROD_EXPLORATION_SLOTS = 3
# "≤ 2 titles per director per slate" — the S2 launch check.
DIRECTOR_SLATE_LIMIT = 2
# A gem label on a title in the top tenth of the catalog by vote count is a
# famous film called a "hidden gem".
FAMOUS_FAME_PERCENTILE = 0.9


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
    # Ratings this title received in the training split (MovieLens). Feeds the
    # in-domain popularity baseline without leaking held-out ratings.
    train_interactions: int = 0

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
            display_names=(self.extra or {}).get("display_names") or {},
        )

    @cached_property
    def directors(self) -> tuple[str, ...]:
        snapshot = (self.extra or {}).get("feature_snapshot") or {}
        return tuple(k for k in snapshot if str(k).startswith("person:director:"))


@dataclass
class EvalUser:
    user_id: str
    # (title_id, event_type, weight, timestamp) — the shape the domain expects.
    train_events: list[tuple[UUID, str, float, datetime]]
    test_positive_ids: set[UUID]
    # Optional graded relevance (rating → gain) for NDCG.
    test_gains: dict[UUID, float] = field(default_factory=dict)
    # Held-out titles the user disliked: a good ranker puts these low.
    test_negative_ids: set[UUID] = field(default_factory=set)


@dataclass
class Dataset:
    """``titles`` are the rankable catalog; ``profile_only_titles`` can be rated
    (they shape profiles) but are not candidates — a production-sized catalog
    is smaller than everything the evaluation users have rated."""

    name: str
    titles: list[EvalTitle]
    users: list[EvalUser]
    profile_only_titles: list[EvalTitle] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    @cached_property
    def by_id(self) -> dict[UUID, EvalTitle]:
        return {t.id: t for t in [*self.profile_only_titles, *self.titles]}

    @cached_property
    def catalog_index(self) -> dict[UUID, int]:
        return {t.id: i for i, t in enumerate(self.titles)}

    @cached_property
    def unit_matrix(self) -> np.ndarray:
        """L2-normalised catalog vectors (zero rows for unembedded titles)."""
        dim = next((len(t.embedding) for t in self.titles if t.embedding), 0)
        matrix = np.zeros((len(self.titles), dim), dtype=np.float64)
        for i, title in enumerate(self.titles):
            if title.embedding is not None and len(title.embedding) == dim:
                matrix[i] = title.embedding
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        np.divide(matrix, norms, out=matrix, where=norms > 1e-12)
        return matrix

    @cached_property
    def popularity_order(self) -> np.ndarray:
        """Catalog indices, most popular first."""
        popularity = np.asarray([t.popularity for t in self.titles], dtype=np.float64)
        return np.argsort(-popularity, kind="stable")

    @cached_property
    def popularity_percentiles(self) -> dict[UUID, float]:
        """0.0 for the most popular title, 1.0 for the least."""
        return _percentiles(self.titles, key=lambda t: t.popularity)

    @cached_property
    def fame_percentiles(self) -> dict[UUID, float]:
        """By vote count: 1.0 for the most-voted title (the most famous), 0.0 for the least."""
        least_first = _percentiles(self.titles, key=lambda t: t.vote_count)
        return {tid: 1.0 - value for tid, value in least_first.items()}


StrategyResult = Sequence[UUID] | Sequence[RankedItem]
Strategy = Callable[[EvalUser, Dataset, int], StrategyResult]


# ------------------------------------------------------------ user context


class _UserContext:
    """Profile and full-catalog channels for one user, shared by all strategies."""

    def __init__(self, user: EvalUser, dataset: Dataset) -> None:
        self.user = user
        self.dataset = dataset
        signals: Mapping[UUID, EffectiveSignal] = effective_title_signals(
            user.train_events, now=datetime.now(UTC), half_life_days=None
        )
        titles = {
            tid: dataset.by_id[tid].as_profile_title() for tid in signals if tid in dataset.by_id
        }
        built = build_profile(signals.values(), titles)
        self.vector = built.vector
        self.features = built.features
        self.seen = {event[0] for event in user.train_events}

    @cached_property
    def channels(self) -> CandidateChannels | None:
        return candidate_channels(
            user_vector=self.vector,
            user_features=self.features,
            titles=self.dataset.titles,
            exclude_ids=self.seen,
        )

    @cached_property
    def channel_index(self) -> dict[UUID, int]:
        channels = self.channels
        return {} if channels is None else {t.id: i for i, t in enumerate(channels.pool)}

    def production_pool(self, k: int) -> list[int]:
        """Channel indices of the candidates production would retrieve.

        Exact cosine stands in for the HNSW index, so this is a slightly
        optimistic version of production's recall-limited retrieval.
        """
        dataset = self.dataset
        if self.vector is not None and dataset.unit_matrix.shape[1] == len(self.vector):
            ann_limit = max(PROD_ANN_CANDIDATES, k * 4) + min(len(self.seen), PROD_ANN_OVERFETCH_MAX)
            user = np.asarray(self.vector, dtype=np.float64)
            user /= max(float(np.linalg.norm(user)), 1e-12)
            cosine = dataset.unit_matrix @ user
            ann = np.argsort(-cosine, kind="stable")[:ann_limit]
            popular = dataset.popularity_order[: max(PROD_POPULAR_CANDIDATES, 20)]
            picked = [*ann.tolist(), *popular.tolist()]
        else:
            limit = max(PROD_ANN_CANDIDATES + PROD_POPULAR_CANDIDATES, PROD_COLD_POOL) + min(
                len(self.seen), PROD_ANN_OVERFETCH_MAX
            )
            picked = dataset.popularity_order[:limit].tolist()
        index = self.channel_index
        out: list[int] = []
        seen_idx: set[int] = set()
        for catalog_i in picked:
            i = index.get(dataset.titles[catalog_i].id)
            if i is not None and i not in seen_idx:
                seen_idx.add(i)
                out.append(i)
        return out


_CONTEXT: list[_UserContext | None] = [None]


def _context(user: EvalUser, dataset: Dataset) -> _UserContext:
    """Memo of size one: ``evaluate`` runs every strategy for a user in turn."""
    ctx = _CONTEXT[0]
    if ctx is None or ctx.user is not user or ctx.dataset is not dataset:
        ctx = _UserContext(user, dataset)
        _CONTEXT[0] = ctx
    return ctx


def _percentile_ranks(scores: np.ndarray, positions: Sequence[int]) -> list[float]:
    """0.0 = ranked first among all candidates, 1.0 = ranked last (ties share the better rank)."""
    n = len(scores)
    if n <= 1:
        return [0.0 for _ in positions]
    return [float(np.count_nonzero(scores > scores[i])) / (n - 1) for i in positions]


# -------------------------------------------------------------- strategies


@dataclass(frozen=True)
class RankingStrategy:
    """The production ranker with a given configuration.

    ``candidate_pool="catalog"`` ranks every unseen title (an upper bound);
    ``"production"`` ranks only what the API's candidate generation retrieves.
    """

    weights: RankingWeights = DEFAULT_WEIGHTS
    mmr_lambda: float = 0.7
    exploration_slots: int = 0
    max_per_genre: int | None = None
    candidate_pool: str = "catalog"

    def __call__(self, user: EvalUser, dataset: Dataset, k: int) -> list[RankedItem]:
        ctx = _context(user, dataset)
        channels = ctx.channels
        if channels is None:
            return []
        if self.candidate_pool == "production":
            channels = channels.subset(ctx.production_pool(k))
        return select_slate(
            channels,
            blend_scores(channels, self.weights),
            user_features=ctx.features,
            slate_size=k,
            mmr_lambda=self.mmr_lambda,
            exploration_slots=self.exploration_slots,
            max_per_genre=self.max_per_genre,
            with_reasons=True,
        )

    def rank_percentiles(
        self, user: EvalUser, dataset: Dataset, ids: Sequence[UUID]
    ) -> dict[UUID, float]:
        """Where the *score* puts each id among all unseen catalog titles."""
        ctx = _context(user, dataset)
        if ctx.channels is None:
            return {}
        scores = blend_scores(ctx.channels, self.weights)
        found = [(tid, ctx.channel_index[tid]) for tid in ids if tid in ctx.channel_index]
        ranks = _percentile_ranks(scores, [i for _tid, i in found])
        return {tid: rank for (tid, _i), rank in zip(found, ranks, strict=True)}


def make_ranking_strategy(
    *,
    weights: RankingWeights = DEFAULT_WEIGHTS,
    mmr_lambda: float = 0.7,
    exploration_slots: int = 0,
    max_per_genre: int | None = None,
    candidate_pool: str = "catalog",
) -> RankingStrategy:
    """The production ranker with a given configuration."""
    return RankingStrategy(
        weights=weights,
        mmr_lambda=mmr_lambda,
        exploration_slots=exploration_slots,
        max_per_genre=max_per_genre,
        candidate_pool=candidate_pool,
    )


@dataclass(frozen=True)
class OrderStrategy:
    """Unseen titles in a fixed order: the popularity baselines."""

    key: Callable[[EvalTitle], float]

    def _scores(self, dataset: Dataset) -> np.ndarray:
        return np.asarray([self.key(t) for t in dataset.titles], dtype=np.float64)

    def __call__(self, user: EvalUser, dataset: Dataset, k: int) -> list[UUID]:
        seen = _seen(user)
        ordered = sorted(dataset.titles, key=self.key, reverse=True)
        return [t.id for t in ordered if t.id not in seen][:k]

    def rank_percentiles(
        self, user: EvalUser, dataset: Dataset, ids: Sequence[UUID]
    ) -> dict[UUID, float]:
        seen = _seen(user)
        unseen = [i for i, t in enumerate(dataset.titles) if t.id not in seen]
        scores = self._scores(dataset)[unseen]
        position = {dataset.titles[i].id: p for p, i in enumerate(unseen)}
        found = [(tid, position[tid]) for tid in ids if tid in position]
        ranks = _percentile_ranks(scores, [p for _tid, p in found])
        return {tid: rank for (tid, _p), rank in zip(found, ranks, strict=True)}


def _seen(user: EvalUser) -> set[UUID]:
    return {event[0] for event in user.train_events}


popularity_strategy = OrderStrategy(key=lambda t: t.popularity)
# In-domain popularity: what other users rated most in the training split.
rating_count_strategy = OrderStrategy(key=lambda t: float(t.train_interactions))


def random_strategy(user: EvalUser, dataset: Dataset, k: int) -> list[UUID]:
    seen = _seen(user)
    pool = [t.id for t in dataset.titles if t.id not in seen]
    # zlib.crc32, not hash(): str hashes are salted per process, which made
    # the random baseline change between runs.
    rng = random.Random(zlib.crc32(user.user_id.encode("utf-8")))
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
    "hybrid": make_ranking_strategy(mmr_lambda=PROD_MMR_LAMBDA),
    "hybrid_with_exploration": make_ranking_strategy(
        mmr_lambda=PROD_MMR_LAMBDA, exploration_slots=PROD_EXPLORATION_SLOTS
    ),
    # Not the default: shows what an accuracy-first popularity prior costs in novelty.
    "hybrid_plus_popularity": make_ranking_strategy(
        weights=RankingWeights(warm_popularity=0.2), mmr_lambda=PROD_MMR_LAMBDA
    ),
    # What the API serves: production candidate pool, MMR, genre cap, exploration.
    "production": make_ranking_strategy(
        mmr_lambda=PROD_MMR_LAMBDA,
        exploration_slots=PROD_EXPLORATION_SLOTS,
        candidate_pool="production",
    ),
}


def production_variant(**weight_overrides: float) -> RankingStrategy:
    """``production`` with some weights changed — for sweeps and proposals."""
    return make_ranking_strategy(
        weights=replace(DEFAULT_WEIGHTS, **weight_overrides),
        mmr_lambda=PROD_MMR_LAMBDA,
        exploration_slots=PROD_EXPLORATION_SLOTS,
        candidate_pool="production",
    )


# -------------------------------------------------------------- evaluation


@dataclass
class EvaluationRun:
    """Per-strategy summaries plus the per-user rows behind them (for paired tests)."""

    k: int
    user_ids: list[str]
    per_user: dict[str, list[dict[str, float | None]]]
    summary: dict[str, dict[str, float]]

    def metric(self, strategy: str, name: str) -> list[float | None]:
        return [row.get(name) for row in self.per_user[strategy]]


def _gem_labelled(items: Sequence[Any]) -> list[UUID] | None:
    """Ids carrying the user-facing "hidden gem" reason; None if the strategy gives no reasons."""
    if not items or not isinstance(items[0], RankedItem):
        return None
    return [
        item.title_id for item in items if any(r.code == "hidden_gem" for r in item.reasons)
    ]


def _user_row(
    *,
    strategy: Strategy,
    user: EvalUser,
    dataset: Dataset,
    k: int,
    produced: StrategyResult,
) -> tuple[list[UUID], dict[str, float | None]]:
    catalog = dataset.by_id
    recommended = [item.title_id if isinstance(item, RankedItem) else item for item in produced]
    relevant = user.test_positive_ids
    gains = user.test_gains or {tid: 1.0 for tid in relevant}
    slate_titles = [catalog[tid] for tid in recommended if tid in catalog]
    vectors = [t.embedding for t in slate_titles if t.embedding is not None]

    row: dict[str, float | None] = {
        f"recall@{k}": M.recall_at_k(recommended, relevant, k=k),
        f"precision@{k}": M.precision_at_k(recommended, relevant, k=k),
        f"ndcg@{k}": M.ndcg_at_k(recommended, gains, k=k),
        f"hit_rate@{k}": M.hit_rate_at_k(recommended, relevant, k=k),
        "mrr": M.reciprocal_rank(recommended, relevant),
        "diversity": M.intra_list_diversity(vectors),
        "genre_variety": M.distinct_ratio(primary_genre(t) for t in slate_titles),
        "novelty": M.novelty(recommended, dataset.popularity_percentiles),
    }

    # Director concentration (S2): the most titles one director holds in the slate.
    directors = Counter(d for t in slate_titles for d in t.directors)
    top_director = max(directors.values(), default=0)
    row["director_max"] = float(top_director)
    row["director_over_limit"] = 1.0 if top_director > DIRECTOR_SLATE_LIMIT else 0.0

    # Negative feedback: held-out dislikes should not be recommended, and the
    # score should place them below held-out likes.
    negatives = user.test_negative_ids
    row[f"dislike_recall@{k}"] = M.recall_at_k(recommended, negatives, k=k) if negatives else None
    rank_percentiles = getattr(strategy, "rank_percentiles", None)
    row["like_rank_pct"] = row["dislike_rank_pct"] = row["like_dislike_auc"] = None
    if rank_percentiles is not None and (relevant or negatives):
        ranks = rank_percentiles(user, dataset, [*relevant, *negatives])
        like = [ranks[t] for t in relevant if t in ranks]
        dislike = [ranks[t] for t in negatives if t in ranks]
        row["like_rank_pct"] = mean(like) if like else None
        row["dislike_rank_pct"] = mean(dislike) if dislike else None
        # Ranks are "lower is better"; AUC wants "higher score is better".
        row["like_dislike_auc"] = (
            M.pairwise_auc([-r for r in like], [-r for r in dislike]) if like and dislike else None
        )

    # Hidden-gem labels: how many per slate, and how famous the labelled titles are.
    gems = _gem_labelled(produced)
    row["gem_labels"] = row["gem_fame_pct"] = row["gem_famous_share"] = None
    if gems is not None:
        row["gem_labels"] = float(len(gems))
        fame = [dataset.fame_percentiles[t] for t in gems if t in dataset.fame_percentiles]
        if fame:
            row["gem_fame_pct"] = mean(fame)
            row["gem_famous_share"] = sum(f >= FAMOUS_FAME_PERCENTILE for f in fame) / len(fame)
    return recommended, row


def run_evaluation(
    dataset: Dataset,
    strategies: Mapping[str, Strategy] | None = None,
    *,
    k: int = DEFAULT_K,
    progress: Callable[[int, int], None] | None = None,
) -> EvaluationRun:
    """Run every strategy for every user (users outer, so per-user work is shared)."""
    strategies = strategies or DEFAULT_STRATEGIES
    users = [u for u in dataset.users if u.test_positive_ids]
    per_user: dict[str, list[dict[str, float | None]]] = {name: [] for name in strategies}
    slates: dict[str, list[list[UUID]]] = {name: [] for name in strategies}

    for done, user in enumerate(users, start=1):
        for name, strategy in strategies.items():
            produced = strategy(user, dataset, k)
            recommended, row = _user_row(
                strategy=strategy, user=user, dataset=dataset, k=k, produced=produced
            )
            per_user[name].append(row)
            slates[name].append(recommended)
        if progress is not None:
            progress(done, len(users))
    _CONTEXT[0] = None

    summary: dict[str, dict[str, float]] = {}
    for name, rows in per_user.items():
        if not rows:
            continue
        metrics: dict[str, float] = {}
        for metric in rows[0]:
            values = [row[metric] for row in rows if row[metric] is not None]
            metrics[metric] = mean(values) if values else float("nan")
        metrics["coverage"] = M.catalog_coverage(slates[name], len(dataset.titles))
        metrics["users"] = float(len(rows))
        summary[name] = metrics
    return EvaluationRun(
        k=k, user_ids=[u.user_id for u in users], per_user=per_user, summary=summary
    )


def evaluate(
    dataset: Dataset,
    strategies: Mapping[str, Strategy] | None = None,
    *,
    k: int = DEFAULT_K,
) -> dict[str, dict[str, float]]:
    """Run every strategy over every user; return metric → value per strategy."""
    return run_evaluation(dataset, strategies, k=k).summary


def paired_deltas(
    run: EvaluationRun,
    *,
    baseline: str,
    metrics: Sequence[str],
    n_boot: int = 2000,
    seed: int = 0,
) -> dict[str, dict[str, tuple[float, float, float, int]]]:
    """strategy → metric → (mean delta vs baseline, 95% CI low, high, users).

    Paired by user, so the noise between users (large at a few hundred users)
    cancels; a CI that excludes 0 is a difference this sample supports.
    """
    out: dict[str, dict[str, tuple[float, float, float, int]]] = {}
    for name in run.per_user:
        if name == baseline:
            continue
        out[name] = {
            metric: M.paired_bootstrap(
                run.metric(baseline, metric), run.metric(name, metric), n_boot=n_boot, seed=seed
            )
            for metric in metrics
        }
    return out


def _percentiles(titles: Sequence[EvalTitle], *, key: Callable[[EvalTitle], float]) -> dict[UUID, float]:
    """0.0 for the title with the highest ``key``, 1.0 for the lowest."""
    if not titles:
        return {}
    ordered = sorted(titles, key=key, reverse=True)
    last = max(len(ordered) - 1, 1)
    return {title.id: index / last for index, title in enumerate(ordered)}



ACCURACY_COLUMNS = ("recall@{k}", "ndcg@{k}", "hit_rate@{k}", "mrr")
BEYOND_COLUMNS = ("diversity", "genre_variety", "novelty", "coverage")
SAFETY_COLUMNS = (
    "dislike_recall@{k}",
    "like_rank_pct",
    "dislike_rank_pct",
    "like_dislike_auc",
    "director_max",
    "director_over_limit",
    "gem_labels",
    "gem_fame_pct",
    "gem_famous_share",
)


def format_report(
    results: Mapping[str, Mapping[str, float]],
    *,
    k: int = DEFAULT_K,
    columns: Sequence[str] | None = None,
) -> str:
    """Markdown table, best strategy per metric is easy to eyeball."""
    if not results:
        return "(no results)"
    columns = [c.format(k=k) for c in (columns or (*ACCURACY_COLUMNS, *BEYOND_COLUMNS))]
    lines = [
        "| strategy | " + " | ".join(columns) + " |",
        "|" + "---|" * (len(columns) + 1),
    ]
    for name, row in results.items():
        cells = []
        for column in columns:
            value = row.get(column, float("nan"))
            cells.append("–" if value != value else f"{value:.3f}")  # NaN → dash
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def format_deltas(
    deltas: Mapping[str, Mapping[str, tuple[float, float, float, int]]],
    *,
    baseline: str,
) -> str:
    """Markdown table of paired deltas with 95% bootstrap intervals."""
    if not deltas:
        return "(no deltas)"
    metrics = list(next(iter(deltas.values())))
    lines = [
        f"| vs `{baseline}` | " + " | ".join(metrics) + " |",
        "|" + "---|" * (len(metrics) + 1),
    ]
    for name, row in deltas.items():
        cells = []
        for metric in metrics:
            delta, low, high, n = row[metric]
            cells.append("–" if n == 0 else f"{delta:+.4f} [{low:+.4f}, {high:+.4f}]")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
