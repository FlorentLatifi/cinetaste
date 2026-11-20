"""Ranking: score candidates, diversify, add exploration, explain.

Pure functions over title-like objects (``id``, ``embedding``, ``extra``,
``genres``, ``popularity``, ``vote_average``, optional ``vote_count``/``name``),
so the same code runs in the API, in unit tests and in offline evaluation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import numpy as np

from app.recommendation.embeddings import sparse_channel_scores
from app.recommendation.explanations import (
    Reason,
    build_reasons,
    strip_explain_memory,
)

# Re-export for callers/tests that import Reason from pipeline.
__all__ = [
    "DEFAULT_WEIGHTS",
    "Reason",
    "RankedItem",
    "RankingWeights",
    "annotate_discovery_reasons",
    "catalog_percentiles",
    "director_cap_for",
    "directors_of",
    "fame",
    "genre_cap_for",
    "explain",
    "gem_boost",
    "low_rating_penalty",
    "mmr_select",
    "percent_rank",
    "primary_genre",
    "rank_titles",
]


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """Blend of dense similarity, sparse (explainable) overlap and priors.

    Chosen with ``python -m app.scripts.evaluate_recommender``
    (docs/EVALUATION.md). On real MovieLens ratings, moving weight from the
    hashed content vector (0.42 → 0.2) to the interpretable feature overlap
    (0.40 → 0.6) tripled recall@20 and cost nothing on the synthetic
    benchmark — the sparse channel is the stronger content signal.
    """

    similarity: float = 0.2
    sparse_positive: float = 0.6
    sparse_negative: float = 0.12
    hidden_gem: float = 1.0
    cold_popularity: float = 1.0
    # Popularity prior for established users, applied to the popularity
    # *percentile within the candidate pool* (scale-free across TMDb and other
    # sources). Off by default on purpose: on MovieLens 0.2 raised recall@20
    # a further ~70% but dropped novelty to ~0.04 — close to recommending the
    # charts, which defeats a discovery product. Kept as a documented knob.
    warm_popularity: float = 0.0
    # Soft penalty for titles many people rated poorly (low_rating_penalty).
    # Kept off by default — see docs/EVALUATION.md for the measurement.
    low_rating: float = 0.0


DEFAULT_WEIGHTS = RankingWeights()

# Diversity: share of one slate that may come from a single primary genre.
# Measured on the synthetic benchmark (docs/EVALUATION.md): a fixed cap of 4
# cost ~60% of recall@20, while 40% of the slate keeps most of the accuracy
# and still shows about four different genres in twenty cards.
GENRE_CAP_RATIO = 0.4
MIN_GENRE_CAP = 3


def genre_cap_for(slate_size: int) -> int:
    """Per-genre cap that means the same thing at any slate size."""
    return max(MIN_GENRE_CAP, round(slate_size * GENRE_CAP_RATIO))


# Diversity: titles per director in one slate. Two in twenty cards; a slate of
# six Nolan films reads as "the algorithm found one name", not as taste.
# Applied to every director of a co-directed film.
DIRECTOR_CAP_RATIO = 0.1
MIN_DIRECTOR_CAP = 1


def director_cap_for(slate_size: int) -> int:
    """Per-director cap: 2 for a 20-card slate, scaled with slate size."""
    return max(MIN_DIRECTOR_CAP, round(slate_size * DIRECTOR_CAP_RATIO))


# Exploration picks must still be well rated.
EXPLORATION_MIN_VOTE = 6.8
# Profiles with less positive mass than this get a popularity prior.
COLD_START_POSITIVE_MASS = 0.8
# Cold-start prior for the best-known title in the catalog; scales linearly
# with how well known a title is (``fame``, a catalog percentile).
COLD_PRIOR_MAX = 0.22

# Where a title sits in the *whole catalog*, written into ``Title.extra`` by
# ingest / re-embed (app/application/catalog_percentiles.py):
#   extra["catalog_percentiles"] = {"popularity": 0.0..1.0, "vote_count": 0.0..1.0}
# 0.0 = least popular / fewest votes, 1.0 = most. Percentiles instead of raw
# TMDb numbers because TMDb popularity is a trending score with no fixed
# scale: in our catalog most titles sit below 40, The Godfather is ~48, The
# Silence of the Lambs ~7.5 and one outlier ~2,500 — absolute cut-offs called
# The Godfather Part II a "hidden gem".
CATALOG_PERCENTILES_KEY = "catalog_percentiles"

# Ratings from a handful of votes are noise; don't call those "hidden gems".
GEM_MIN_VOTES = 50
# ...and relative to the catalog: the bottom tenth by vote count is too thin to trust.
GEM_MIN_VOTE_PCT = 0.10
# A hidden gem is less known than most of the catalog on *both* popularity and
# vote count ("fame" = the higher of the two percentiles). Vote count matters:
# classics with a low trending score (Silence of the Lambs) have huge vote
# counts and are not hidden.
GEM_MAX_FAME_PCT = 0.40
GEM_STRONG_BONUS = 0.08
# Very well rated and only moderately known: a smaller bonus, never the label.
GEM_SOFT_MAX_FAME_PCT = 0.60
GEM_SOFT_BONUS = 0.04
# How many picks per slate may carry the "hidden gem" label. The score bonus
# still applies to every qualifying title; only the label is rationed. Most
# well-rated films are not blockbusters, so without a cap whole slates came
# back labelled "hidden gem" and the badge stopped meaning anything.
MAX_GEM_LABELS = 3

# Soft penalty for titles a large audience rated poorly (★ below 6.5 with
# enough votes to mean it). Off by default: see RankingWeights.low_rating.
LOW_RATING_THRESHOLD = 6.5
LOW_RATING_SPAN = 1.0  # penalty reaches its full weight at ★5.5


def fame(popularity_pct: float, vote_count_pct: float | None = None) -> float:
    """How well known a title is, 0..1: the higher of its two catalog percentiles."""
    if vote_count_pct is None or vote_count_pct != vote_count_pct:  # None or NaN
        return float(popularity_pct)
    return float(max(popularity_pct, vote_count_pct))


def _enough_votes(vote_count: int | None, vote_count_pct: float | None) -> bool:
    if vote_count is not None and vote_count < GEM_MIN_VOTES:
        return False
    if vote_count_pct is not None and vote_count_pct == vote_count_pct:
        return vote_count_pct >= GEM_MIN_VOTE_PCT
    return True


def gem_boost(
    vote_average: float,
    *,
    popularity_pct: float,
    vote_count_pct: float | None = None,
    vote_count: int | None = None,
) -> float:
    """Quality-vs-fame bonus for under-the-radar titles (hidden gems).

    Percentiles are catalog-relative (0.0 = least known), so the rule means
    the same thing whatever TMDb's popularity scale does this month.
    """
    if not _enough_votes(vote_count, vote_count_pct):
        return 0.0
    known = fame(popularity_pct, vote_count_pct)
    if vote_average >= 7.2 and known < GEM_MAX_FAME_PCT:
        return GEM_STRONG_BONUS
    if vote_average >= 7.5 and known < GEM_SOFT_MAX_FAME_PCT:
        return GEM_SOFT_BONUS
    return 0.0


def low_rating_penalty(
    vote_average: float,
    *,
    vote_count: int | None = None,
    vote_count_pct: float | None = None,
) -> float:
    """0..1: how far below ★6.5 a title is, if enough people rated it."""
    if not _enough_votes(vote_count, vote_count_pct):
        return 0.0
    return float(min(max((LOW_RATING_THRESHOLD - vote_average) / LOW_RATING_SPAN, 0.0), 1.0))


def percent_rank(values: Sequence[float] | np.ndarray) -> np.ndarray:
    """Share of the other values strictly below each value (SQL ``percent_rank``)."""
    arr = np.asarray(values, dtype=np.float64)
    n = len(arr)
    if n <= 1:
        return np.zeros(n, dtype=np.float64)
    return np.searchsorted(np.sort(arr), arr, side="left") / float(n - 1)


def catalog_percentiles(titles: Sequence[Any]) -> tuple[np.ndarray, np.ndarray]:
    """(popularity, vote_count) percentiles for each title; vote_count NaN if unknown.

    Uses the catalog-wide values stored in ``extra`` when present. Otherwise
    falls back to the percentile *within these titles* — fine for offline
    evaluation (the pool is the catalog) but biased in production, where the
    pool is nearest neighbours plus the popular head; hence the stored values.
    """
    n = len(titles)
    popularity = percent_rank([float(t.popularity or 0.0) for t in titles])
    votes = np.full(n, np.nan, dtype=np.float64)
    known = [(i, float(v)) for i, t in enumerate(titles) if (v := getattr(t, "vote_count", None)) is not None]
    if known:
        votes[[i for i, _ in known]] = percent_rank([v for _, v in known])

    for i, title in enumerate(titles):
        stored = (getattr(title, "extra", None) or {}).get(CATALOG_PERCENTILES_KEY)
        if not isinstance(stored, dict):
            continue
        for key, target in (("popularity", popularity), ("vote_count", votes)):
            try:
                value = float(stored[key])
            except (KeyError, TypeError, ValueError):
                continue
            if 0.0 <= value <= 1.0:
                target[i] = value
    return popularity, votes


def annotate_discovery_reasons(
    reasons: list[Reason],
    *,
    is_hidden_gem: bool,
    is_exploration: bool,
    vote_average: float,
    popularity: float,
    title_name: str | None = None,
    max_reasons: int = 3,
) -> list[Reason]:
    """Ensure exploration / hidden-gem picks get explicit, user-facing reasons."""
    codes = {r.code for r in reasons}
    extra: list[Reason] = []

    if is_hidden_gem and "hidden_gem" not in codes:
        extra.append(
            Reason(
                code="hidden_gem",
                message=(
                    f"Highly rated (★{vote_average:.1f}) but not a chart-topper "
                    "— a hidden gem"
                ),
                evidence={
                    "vote_average": round(float(vote_average), 2),
                    "popularity": round(float(popularity), 2),
                },
            )
        )

    if is_exploration and "discovery" not in codes:
        extra.append(
            Reason(
                code="discovery",
                message="An exploration pick to stretch beyond your usual favorites",
                evidence={"candidate": title_name, "slot": "exploration"},
            )
        )

    if not extra:
        return reasons[:max_reasons]

    # Prefer primary taste reason first, then discovery annotations.
    if reasons:
        primary, rest = reasons[0], reasons[1:]
        merged = [primary, *extra, *rest]
    else:
        merged = [*extra]
    seen: set[str] = set()
    out: list[Reason] = []
    for r in merged:
        if r.code in seen and r.code not in {"because_you_liked", "thematic_bridge"}:
            continue
        seen.add(r.code)
        out.append(r)
        if len(out) >= max_reasons:
            break
    return out


class RankedItem:
    __slots__ = ("title_id", "score", "reasons", "hidden_gem")

    def __init__(
        self,
        title_id: UUID,
        score: float,
        reasons: list[Reason],
        *,
        hidden_gem: bool = False,
    ) -> None:
        self.title_id = title_id
        self.score = score
        self.reasons = reasons
        # Carries the "hidden gem" label decision even when reasons are not
        # built (offline evaluation), so label quality can be measured.
        self.hidden_gem = hidden_gem


def _feature_snapshot(title_extra: dict[str, Any] | None) -> dict[str, float]:
    snap = (title_extra or {}).get("feature_snapshot") or {}
    out: dict[str, float] = {}
    for k, v in snap.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


DIRECTOR_PREFIX = "person:director:"


def directors_of(title: Any) -> tuple[str, ...]:
    """Normalised director names from the feature snapshot (empty for most TV)."""
    snapshot = (getattr(title, "extra", None) or {}).get("feature_snapshot") or {}
    return tuple(
        sorted(key[len(DIRECTOR_PREFIX) :] for key in snapshot if key.startswith(DIRECTOR_PREFIX))
    )


def primary_genre(title: Any) -> str:
    """Main genre: the highest-weighted genre in the feature snapshot.

    The snapshot keeps TMDb's genre order (first genre weighs most); the ORM
    ``genres`` relationship is unordered, so it is only a fallback.
    """
    best_key, best_weight = "", 0.0
    for key, weight in _feature_snapshot(getattr(title, "extra", None)).items():
        if key.startswith("genre:") and weight > best_weight:
            best_key, best_weight = key, weight
    if best_key:
        return best_key[len("genre:") :]
    genres = getattr(title, "genres", None) or []
    return str(genres[0].name).lower() if genres else "unknown"


def explain(
    *,
    user_features: dict[str, Any],
    title_extra: dict[str, Any] | None,
    title_genres: list[str],
    similarity: float,
    explain_memory: dict[str, Any] | None = None,
    title_name: str | None = None,
) -> list[Reason]:
    """Backward-compatible wrapper around build_reasons."""
    scoring, memory_from_features = strip_explain_memory(user_features)
    memory = explain_memory if explain_memory is not None else memory_from_features
    return build_reasons(
        user_features=scoring,
        explain_memory=memory,
        title_name=title_name,
        title_extra=title_extra,
        title_genres=title_genres,
        similarity=similarity,
    )


def _unit_matrix(vectors: Sequence[Any]) -> np.ndarray:
    """Stack vectors into an L2-normalised matrix; missing ones become zero rows."""
    dim = next((len(v) for v in vectors if v is not None and len(v) > 0), 0)
    matrix = np.zeros((len(vectors), dim), dtype=np.float64)
    for i, vec in enumerate(vectors):
        if vec is not None and len(vec) == dim:
            matrix[i] = np.asarray(vec, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, norms, out=matrix, where=norms > 1e-12)
    return matrix


def _mmr_order(
    relevance: np.ndarray,
    unit: np.ndarray,
    *,
    k: int,
    lambda_mult: float,
    allow: Callable[[int], bool] | None = None,
    on_pick: Callable[[int], None] | None = None,
) -> list[int]:
    """Greedy Maximal Marginal Relevance, optionally under per-slate quotas.

    Each step picks ``argmax(λ·relevance − (1−λ)·max_similarity_to_picked)``
    among candidates ``allow`` accepts (quotas such as the genre and director
    caps); ``on_pick`` is told about every pick so the quotas can count it.
    Vectorised: one matrix-vector product per pick instead of pairwise loops.
    """
    n = len(relevance)
    chosen: list[int] = []
    if n == 0 or k <= 0:
        return chosen
    available = np.ones(n, dtype=bool)
    max_sim = np.zeros(n, dtype=np.float64)

    while len(chosen) < k:
        if chosen:
            mmr = lambda_mult * relevance - (1.0 - lambda_mult) * max_sim
        else:
            mmr = relevance.astype(np.float64, copy=True)
        mmr = np.where(available, mmr, -np.inf)
        pick = -1
        while True:
            best = int(np.argmax(mmr))
            if not np.isfinite(mmr[best]):
                break
            if allow is None or allow(best):
                pick = best
                break
            # Over a cap: quotas only fill up within one ranking, so it never returns.
            available[best] = False
            mmr[best] = -np.inf
        if pick < 0:
            break
        chosen.append(pick)
        available[pick] = False
        np.maximum(max_sim, unit @ unit[pick], out=max_sim)
        if on_pick is not None:
            on_pick(pick)
    return chosen


def mmr_select(
    candidates: list[tuple[UUID, float, Any]],
    *,
    k: int,
    lambda_mult: float = 0.7,
) -> list[UUID]:
    """Maximal Marginal Relevance over ``(id, relevance, vector)`` candidates."""
    if not candidates:
        return []
    relevance = np.asarray([float(rel) for _id, rel, _vec in candidates], dtype=np.float64)
    unit = _unit_matrix([vec for _id, _rel, vec in candidates])
    order = _mmr_order(relevance, unit, k=k, lambda_mult=lambda_mult)
    return [candidates[i][0] for i in order]


class _SlateQuotas:
    """Per-slate caps: titles per primary genre and per director.

    Shared by core MMR, exploration and backfill, so no stage can undo
    another's diversity. A co-directed film counts for each director.
    """

    __slots__ = ("genres", "directors", "genre_cap", "director_cap", "genre_counts", "director_counts")

    def __init__(
        self,
        genres: list[str],
        directors: list[tuple[str, ...]],
        *,
        genre_cap: int,
        director_cap: int,
    ) -> None:
        self.genres = genres
        self.directors = directors
        self.genre_cap = genre_cap
        self.director_cap = director_cap
        self.genre_counts: Counter[str] = Counter()
        self.director_counts: Counter[str] = Counter()

    def allows(self, i: int) -> bool:
        if self.genre_counts[self.genres[i]] >= self.genre_cap:
            return False
        return all(self.director_counts[d] < self.director_cap for d in self.directors[i])

    def add(self, i: int) -> None:
        self.genre_counts[self.genres[i]] += 1
        for director in self.directors[i]:
            self.director_counts[director] += 1


def _interleave(main: list[int], extra: list[int]) -> list[int]:
    """Spread ``extra`` evenly through ``main`` (never at position 0)."""
    if not extra:
        return list(main)
    out = list(main)
    step = max(len(out) // (len(extra) + 1), 1)
    for n, item in enumerate(extra, start=1):
        out.insert(min(n * step + (n - 1), len(out)), item)
    return out


def rank_titles(
    *,
    user_vector: Sequence[float] | None,
    user_features: dict[str, Any],
    titles: list[Any],
    exclude_ids: set[UUID],
    slate_size: int,
    mmr_lambda: float,
    exploration_slots: int = 3,
    explain_memory: dict[str, Any] | None = None,
    weights: RankingWeights = DEFAULT_WEIGHTS,
    max_per_genre: int | None = None,
    max_per_director: int | None = None,
    with_reasons: bool = True,
) -> list[RankedItem]:
    """Rank candidate titles for one user.

    1. Score = weighted dense similarity + sparse overlap − sparse penalty
       + hidden-gem bonus + popularity prior (cold start only)
       − low-rating penalty (off by default).
    2. MMR over the top of the list, with per-primary-genre and per-director caps.
    3. Exploration: well-rated, moderately relevant titles from genres the slate
       doesn't cover yet, spread through the slate, under the same caps.
    4. Backfill by score, still honouring both caps; if the catalog can't fill
       the slate that way, the genre cap is raised one step at a time, and the
       director cap only once the genre cap no longer binds.
    """
    if slate_size <= 0:
        return []
    if max_per_genre is None:
        max_per_genre = genre_cap_for(slate_size)
    if max_per_director is None:
        max_per_director = director_cap_for(slate_size)
    scoring_features, memory_from_features = strip_explain_memory(user_features)
    memory = explain_memory if explain_memory is not None else memory_from_features

    pool: list[Any] = []
    seen_ids: set[UUID] = set()
    for title in titles:
        if title.id in exclude_ids or title.id in seen_ids or title.embedding is None:
            continue
        seen_ids.add(title.id)
        pool.append(title)
    if not pool:
        return []
    n = len(pool)

    unit = _unit_matrix([t.embedding for t in pool])
    user_unit: np.ndarray | None = None
    if user_vector is not None and len(user_vector) == unit.shape[1]:
        vec = np.asarray(user_vector, dtype=np.float64)
        norm = float(np.linalg.norm(vec))
        if norm > 1e-12:
            user_unit = vec / norm
    similarity = unit @ user_unit if user_unit is not None else np.zeros(n)

    positive_mass = sum(v for v in scoring_features.values() if v > 0)
    cold = user_unit is None or positive_mass < COLD_START_POSITIVE_MASS

    snapshots = [_feature_snapshot(t.extra) for t in pool]
    if scoring_features:
        sparse = [sparse_channel_scores(scoring_features, snap) for snap in snapshots]
    else:
        sparse = [(0.0, 0.0)] * n
    positive = np.asarray([p for p, _ in sparse], dtype=np.float64)
    negative = np.asarray([q for _, q in sparse], dtype=np.float64)

    popularity = np.asarray([float(t.popularity) for t in pool], dtype=np.float64)
    popularity_pct, vote_count_pct = catalog_percentiles(pool)
    known = np.fmax(popularity_pct, vote_count_pct)  # fame(); NaN vote pct is ignored
    gems = np.asarray(
        [
            gem_boost(
                float(t.vote_average),
                popularity_pct=float(popularity_pct[i]),
                vote_count_pct=float(vote_count_pct[i]),
                vote_count=getattr(t, "vote_count", None),
            )
            for i, t in enumerate(pool)
        ],
        dtype=np.float64,
    )
    prior = COLD_PRIOR_MAX * known if cold else np.zeros(n)
    low_rated = np.zeros(n)
    if weights.low_rating:
        low_rated = np.asarray(
            [
                low_rating_penalty(
                    float(t.vote_average),
                    vote_count=getattr(t, "vote_count", None),
                    vote_count_pct=float(vote_count_pct[i]),
                )
                for i, t in enumerate(pool)
            ],
            dtype=np.float64,
        )
    warm_prior = np.zeros(n)
    if not cold and weights.warm_popularity and n > 1:
        # Rank-based percentile: 1.0 for the most popular candidate, 0.0 for the least.
        warm_prior = np.argsort(np.argsort(popularity, kind="stable"), kind="stable") / (n - 1)

    scores = (
        weights.similarity * similarity
        + weights.sparse_positive * positive
        - weights.sparse_negative * negative
        + weights.hidden_gem * gems
        + weights.cold_popularity * prior
        + weights.warm_popularity * warm_prior
        - weights.low_rating * low_rated
    )
    order = [int(i) for i in np.argsort(-scores, kind="stable")]

    genres = [primary_genre(t) for t in pool]
    quotas = _SlateQuotas(
        genres,
        [directors_of(t) for t in pool],
        genre_cap=max_per_genre,
        director_cap=max_per_director,
    )

    slots = min(max(int(exploration_slots), 0), max(slate_size - 1, 0))
    core_k = slate_size - slots

    head = np.asarray(order[: max(slate_size * 4, 40)], dtype=np.int64)
    core = [
        int(head[i])
        for i in _mmr_order(
            scores[head],
            unit[head],
            k=core_k,
            lambda_mult=mmr_lambda,
            allow=lambda h: quotas.allows(int(head[h])),
            on_pick=lambda h: quotas.add(int(head[h])),
        )
    ]
    chosen = set(core)

    exploration: list[int] = []
    if slots:
        window = order[slate_size : slate_size * 5]
        eligible = [
            i
            for i in window
            if i not in chosen and float(pool[i].vote_average) >= EXPLORATION_MIN_VOTE
        ]
        # Genres the slate doesn't show yet first — that is what makes it exploration.
        fresh = [i for i in eligible if quotas.genre_counts[genres[i]] == 0]
        familiar = [i for i in eligible if quotas.genre_counts[genres[i]] > 0]
        picked_genres: set[str] = set()
        for candidates, distinct in ((fresh, True), (familiar, False)):
            for i in candidates:
                if len(exploration) >= slots:
                    break
                genre = genres[i]
                if distinct and genre in picked_genres:
                    continue
                if not quotas.allows(i):
                    continue
                exploration.append(i)
                picked_genres.add(genre)
                chosen.add(i)
                quotas.add(i)

    # Backfill by score under both caps. If the slate is still short, raise the
    # genre cap one step at a time, so no genre gets another slot before every
    # genre with candidates left has had its turn. The director cap is raised
    # only once the genre cap can no longer bind: a third film by one director
    # is the last resort, not a side effect of filling a genre.
    backfill: list[int] = []
    need = slate_size - len(core) - len(exploration)
    while need > 0 and len(chosen) < n:
        for i in order:
            if need <= 0:
                break
            if i in chosen or not quotas.allows(i):
                continue
            backfill.append(i)
            chosen.add(i)
            quotas.add(i)
            need -= 1
        if quotas.genre_cap < slate_size:
            quotas.genre_cap += 1
        else:
            quotas.director_cap += 1

    final = _interleave(core + backfill, exploration)[:slate_size]
    exploration_set = set(exploration)
    # Only the strong tier (well rated AND less known than most of the
    # catalog) may say "hidden gem"; the best rated of those keep the label.
    gem_labelled = set(
        sorted(
            (i for i in final if gems[i] >= GEM_STRONG_BONUS),
            key=lambda i: float(pool[i].vote_average),
            reverse=True,
        )[:MAX_GEM_LABELS]
    )

    results: list[RankedItem] = []
    for i in final:
        title = pool[i]
        reasons: list[Reason] = []
        if with_reasons:
            title_name = getattr(title, "name", None)
            reasons = build_reasons(
                user_features=scoring_features,
                explain_memory=memory,
                title_name=title_name,
                title_extra=title.extra,
                title_genres=[g.name for g in title.genres],
                similarity=float(similarity[i]),
            )
            reasons = annotate_discovery_reasons(
                reasons,
                is_hidden_gem=i in gem_labelled,
                is_exploration=i in exploration_set,
                vote_average=float(title.vote_average),
                popularity=float(title.popularity),
                title_name=title_name,
            )
        results.append(
            RankedItem(
                title_id=title.id,
                score=float(scores[i]),
                reasons=reasons,
                hidden_gem=i in gem_labelled,
            )
        )
    return results
