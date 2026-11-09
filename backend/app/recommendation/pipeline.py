"""Ranking: score candidates, diversify, add exploration, explain.

Pure functions over title-like objects (``id``, ``embedding``, ``extra``,
``genres``, ``popularity``, ``vote_average``, optional ``vote_count``/``name``),
so the same code runs in the API, in unit tests and in offline evaluation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
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
    "director_cap_for",
    "genre_cap_for",
    "explain",
    "gem_bonuses",
    "mmr_select",
    "primary_director",
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
    # Quality prior: percentile, within the candidate pool, of a Bayesian
    # (vote-count-shrunk) average rating. Not popularity: an obscure film
    # rated 8.4 by a few hundred people outranks a blockbuster rated 6.9 by
    # thousands. It mostly decides between titles the content signal cannot
    # tell apart — on MovieLens 74% of the catalog shares an identical feature
    # snapshot with another title, and those ties used to be broken by
    # catalog order. Measured on MovieLens (docs/EVALUATION.md): 0.10 raised
    # recall@20 by about half and NDCG by ~45% while keeping novelty ~50%
    # higher than a popularity prior of equal recall; neutral on synthetic.
    quality: float = 0.10


DEFAULT_WEIGHTS = RankingWeights()


def quality_percentiles(
    vote_average: np.ndarray, vote_count: np.ndarray | None
) -> np.ndarray:
    """Share of the pool rated at or below each title, after shrinkage.

    A rating from a handful of votes is pulled toward the pool's mean, with the
    pool's median vote count as the prior's weight (the IMDb top-list formula),
    so ten votes of 9.0 do not beat ten thousand of 8.5.
    """
    n = len(vote_average)
    if n == 0:
        return np.zeros(0)
    if vote_count is None:
        return _share_at_or_below(vote_average)
    prior_weight = max(float(np.median(vote_count)), 1.0)
    prior_mean = float(np.mean(vote_average))
    shrunk = (vote_count * vote_average + prior_weight * prior_mean) / (vote_count + prior_weight)
    return _share_at_or_below(shrunk)

# Diversity: share of one slate that may come from a single primary genre.
# Measured on the synthetic benchmark (docs/EVALUATION.md): a fixed cap of 4
# cost ~60% of recall@20, while 40% of the slate keeps most of the accuracy
# and still shows about four different genres in twenty cards.
GENRE_CAP_RATIO = 0.4
MIN_GENRE_CAP = 3


def genre_cap_for(slate_size: int) -> int:
    """Per-genre cap that means the same thing at any slate size."""
    return max(MIN_GENRE_CAP, round(slate_size * GENRE_CAP_RATIO))


# Diversity across *people*, not just genres. A genre cap cannot see an auteur
# bubble: rating Inception and The Dark Knight highly made the live slate open
# with eight Christopher Nolan films in nine cards, spread across action, war,
# drama and mystery so no genre was ever over its cap. Two films per director
# in a slate of twenty still says "you like Nolan" and leaves room to discover.
DIRECTOR_CAP_RATIO = 0.1
MIN_DIRECTOR_CAP = 2


def director_cap_for(slate_size: int) -> int:
    """Per-director cap: two in a slate of twenty, more for longer slates."""
    return max(MIN_DIRECTOR_CAP, round(slate_size * DIRECTOR_CAP_RATIO))


# Exploration picks must still be well rated.
EXPLORATION_MIN_VOTE = 6.8
# Profiles with less positive mass than this get a popularity prior.
COLD_START_POSITIVE_MASS = 0.8
# Ratings from a handful of votes are noise; don't call those "hidden gems".
GEM_MIN_VOTES = 50
GEM_MIN_RATING = 7.2
GEM_STRONG_RATING = 7.5
# A hidden gem is well rated *and seen by few*: in the bottom share of the
# candidate pool by vote count (how many people have rated it), and not among
# the pool's currently trending titles either. It used to key off TMDb
# `popularity`, which measures this week's buzz, not how widely a film is known
# — so Pulp Fiction (29k votes, little current buzz) shipped labelled "not a
# chart-topper — a hidden gem". Relative to the pool so the same rule holds for
# TMDb, MovieLens and synthetic catalogs alike.
GEM_MAX_REACH_SHARE = 0.35
GEM_MAX_TRENDING_SHARE = 0.6
# How many picks per slate may carry the "hidden gem" label. The score bonus
# still applies to every qualifying title; only the label is rationed. Most
# well-rated films are not blockbusters, so without a cap whole slates came
# back labelled "hidden gem" and the badge stopped meaning anything.
MAX_GEM_LABELS = 3


def _share_at_or_below(values: np.ndarray) -> np.ndarray:
    """For each value, the share of the pool at or below it (ties share a value)."""
    n = len(values)
    if n == 0:
        return np.zeros(0)
    ordered = np.sort(values)
    return np.searchsorted(ordered, values, side="right") / float(n)


def gem_bonuses(
    vote_average: np.ndarray,
    popularity: np.ndarray,
    vote_count: np.ndarray | None = None,
) -> np.ndarray:
    """Ranking bonus for hidden gems: well rated, reliably rated, rarely seen.

    ``vote_count`` measures reach (how many people rated it). Without it —
    some callers only know popularity — popularity stands in for reach.
    """
    n = len(vote_average)
    if n == 0:
        return np.zeros(0)
    reach = vote_count if vote_count is not None else popularity
    reliable = (
        vote_count >= GEM_MIN_VOTES if vote_count is not None else np.ones(n, dtype=bool)
    )
    qualifies = (
        reliable
        & (vote_average >= GEM_MIN_RATING)
        & (_share_at_or_below(reach) <= GEM_MAX_REACH_SHARE)
        & (_share_at_or_below(popularity) <= GEM_MAX_TRENDING_SHARE)
    )
    strength = np.where(vote_average >= GEM_STRONG_RATING, 0.08, 0.04)
    return np.where(qualifies, strength, 0.0)


def primary_director(title: Any) -> str | None:
    """The title's main director (highest-weighted in its feature snapshot)."""
    best_key, best_weight = "", 0.0
    for key, weight in _feature_snapshot(getattr(title, "extra", None)).items():
        if key.startswith("person:director:") and (
            weight > best_weight or (weight == best_weight and key < best_key)
        ):
            best_key, best_weight = key, weight
    return best_key[len("person:director:") :] if best_key else None


def annotate_discovery_reasons(
    reasons: list[Reason],
    *,
    is_hidden_gem: bool,
    is_exploration: bool,
    vote_average: float,
    popularity: float,
    title_name: str | None = None,
    max_reasons: int = 3,
    vote_count: int | None = None,
) -> list[Reason]:
    """Ensure exploration / hidden-gem picks get explicit, user-facing reasons."""
    codes = {r.code for r in reasons}
    extra: list[Reason] = []

    if is_hidden_gem and "hidden_gem" not in codes:
        # Says only what gem_bonuses checked: well rated, and rated by far
        # fewer people than most of the films considered for this slate.
        evidence: dict[str, Any] = {
            "vote_average": round(float(vote_average), 2),
            "popularity": round(float(popularity), 2),
        }
        if vote_count is not None:
            evidence["vote_count"] = int(vote_count)
        extra.append(
            Reason(
                code="hidden_gem",
                message=(
                    f"Rated ★{vote_average:.1f}, yet far less seen than most films "
                    "we considered — a hidden gem"
                ),
                evidence=evidence,
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
    __slots__ = ("title_id", "score", "reasons")

    def __init__(self, title_id: UUID, score: float, reasons: list[Reason]) -> None:
        self.title_id = title_id
        self.score = score
        self.reasons = reasons


def _feature_snapshot(title_extra: dict[str, Any] | None) -> dict[str, float]:
    snap = (title_extra or {}).get("feature_snapshot") or {}
    out: dict[str, float] = {}
    for k, v in snap.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


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
    groups: np.ndarray | None = None,
    max_per_group: int | None = None,
    caps: Sequence[tuple[np.ndarray, int]] = (),
) -> list[int]:
    """Greedy Maximal Marginal Relevance, optionally capping picks per group.

    Each step picks ``argmax(λ·relevance − (1−λ)·max_similarity_to_picked)``.
    Vectorised: one matrix-vector product per pick instead of pairwise loops.

    ``caps`` holds any number of ``(group ids, max picks per group)`` limits —
    genre and director at once, say. A negative id means "no group": titles
    without a known director are never held back by the director cap.
    """
    n = len(relevance)
    chosen: list[int] = []
    if n == 0 or k <= 0:
        return chosen
    limits = list(caps)
    if groups is not None and max_per_group is not None:
        limits.append((groups, max_per_group))
    available = np.ones(n, dtype=bool)
    max_sim = np.zeros(n, dtype=np.float64)
    counters: list[Counter[int]] = [Counter() for _ in limits]

    while len(chosen) < k:
        if chosen:
            mmr = lambda_mult * relevance - (1.0 - lambda_mult) * max_sim
        else:
            mmr = relevance.astype(np.float64, copy=True)
        mmr = np.where(available, mmr, -np.inf)
        pick = int(np.argmax(mmr))
        if not np.isfinite(mmr[pick]):
            break
        chosen.append(pick)
        available[pick] = False
        np.maximum(max_sim, unit @ unit[pick], out=max_sim)
        for (group_ids, cap), counter in zip(limits, counters, strict=True):
            group = int(group_ids[pick])
            if group < 0:
                continue
            counter[group] += 1
            if counter[group] >= cap:
                available &= group_ids != group
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
       + hidden-gem bonus + popularity prior (cold start only).
    2. MMR over the top of the list, with per-primary-genre and per-director caps.
    3. Exploration: well-rated, moderately relevant titles from genres the slate
       doesn't cover yet, spread through the slate.
    4. Backfill by score, still honouring both caps; if the catalog can't
       fill the slate that way, the caps are raised one step at a time.
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
    vote_average = np.asarray([float(t.vote_average) for t in pool], dtype=np.float64)
    raw_counts = [getattr(t, "vote_count", None) for t in pool]
    vote_count = (
        np.asarray([float(c) for c in raw_counts], dtype=np.float64)
        if all(c is not None for c in raw_counts)
        else None
    )
    gems = gem_bonuses(vote_average, popularity, vote_count)
    prior = np.minimum(popularity / 200.0, 0.22) if cold else np.zeros(n)
    warm_prior = np.zeros(n)
    if not cold and weights.warm_popularity and n > 1:
        # Rank-based percentile: 1.0 for the most popular candidate, 0.0 for the least.
        warm_prior = np.argsort(np.argsort(popularity, kind="stable"), kind="stable") / (n - 1)

    quality = (
        quality_percentiles(vote_average, vote_count) if weights.quality else np.zeros(n)
    )

    scores = (
        weights.similarity * similarity
        + weights.sparse_positive * positive
        - weights.sparse_negative * negative
        + weights.hidden_gem * gems
        + weights.cold_popularity * prior
        + weights.warm_popularity * warm_prior
        + weights.quality * quality
    )
    order = [int(i) for i in np.argsort(-scores, kind="stable")]

    genres = [primary_genre(t) for t in pool]
    genre_index = {g: i for i, g in enumerate(dict.fromkeys(genres))}
    genre_ids = np.asarray([genre_index[g] for g in genres], dtype=np.int64)
    # -1 = no known director: never held back by the director cap.
    directors = [primary_director(t) for t in pool]
    director_index = {d: i for i, d in enumerate(dict.fromkeys(d for d in directors if d))}
    director_ids = np.asarray(
        [director_index[d] if d else -1 for d in directors], dtype=np.int64
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
            caps=(
                (genre_ids[head], max_per_genre),
                (director_ids[head], max_per_director),
            ),
        )
    ]
    chosen = set(core)
    counts: Counter[str] = Counter(genres[i] for i in core)
    director_counts: Counter[str] = Counter(directors[i] for i in core if directors[i])

    def director_full(i: int, cap: int) -> bool:
        director = directors[i]
        return director is not None and director_counts[director] >= cap

    def take(i: int) -> None:
        chosen.add(i)
        counts[genres[i]] += 1
        if directors[i]:
            director_counts[directors[i]] += 1

    exploration: list[int] = []
    if slots:
        window = order[slate_size : slate_size * 5]
        eligible = [
            i
            for i in window
            if i not in chosen and float(pool[i].vote_average) >= EXPLORATION_MIN_VOTE
        ]
        # Genres the slate doesn't show yet first — that is what makes it exploration.
        fresh = [i for i in eligible if counts[genres[i]] == 0]
        familiar = [i for i in eligible if counts[genres[i]] > 0]
        picked_genres: set[str] = set()
        for candidates, distinct in ((fresh, True), (familiar, False)):
            for i in candidates:
                if len(exploration) >= slots:
                    break
                genre = genres[i]
                if distinct and genre in picked_genres:
                    continue
                if counts[genre] >= max_per_genre or director_full(i, max_per_director):
                    continue
                exploration.append(i)
                picked_genres.add(genre)
                take(i)

    # Backfill by score under both caps. If the slate is still short, raise the
    # caps one step at a time, so no genre or director gets another slot before
    # every one with candidates left has had its turn.
    backfill: list[int] = []
    need = slate_size - len(core) - len(exploration)
    cap, director_cap = max_per_genre, max_per_director
    while need > 0 and len(chosen) < n:
        for i in order:
            if need <= 0:
                break
            if i in chosen or counts[genres[i]] >= cap or director_full(i, director_cap):
                continue
            backfill.append(i)
            take(i)
            need -= 1
        cap += 1
        director_cap += 1

    final = _interleave(core + backfill, exploration)[:slate_size]
    exploration_set = set(exploration)
    # The strongest gems keep the label: biggest bonus, then best rated.
    gem_labelled = set(
        sorted(
            (i for i in final if gems[i] > 0),
            key=lambda i: (gems[i], float(pool[i].vote_average)),
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
                vote_count=getattr(title, "vote_count", None),
            )
        results.append(RankedItem(title_id=title.id, score=float(scores[i]), reasons=reasons))
    return results
