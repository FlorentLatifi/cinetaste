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
    "CandidateChannels",
    "Reason",
    "RankedItem",
    "RankingWeights",
    "annotate_discovery_reasons",
    "blend_scores",
    "candidate_channels",
    "catalog_percentiles",
    "collection_cap_for",
    "collection_of",
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
    "quality_percentiles",
    "rank_titles",
    "select_slate",
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
    # Penalty for overlap with features the user dislikes. Was 0.12; on real
    # MovieLens ratings with TMDb metadata, 0.5 separates held-out likes from
    # held-out dislikes better (AUC +0.01, under the old and the new slate
    # rules) at no accuracy cost. Past ~0.5 it starts pushing liked titles down,
    # because likes and dislikes share genres, decades and cast.
    sparse_negative: float = 0.5
    hidden_gem: float = 1.0
    cold_popularity: float = 1.0
    # Popularity prior for established users, applied to the popularity
    # *percentile within the candidate pool* (scale-free across TMDb and other
    # sources). Off by default on purpose: on MovieLens 0.2 raised recall@20
    # a further ~70% but dropped novelty to ~0.04 — close to recommending the
    # charts, which defeats a discovery product. Kept as a documented knob.
    warm_popularity: float = 0.0
    # Soft penalty for titles many people rated poorly (low_rating_penalty).
    # Off by default: see docs/EVALUATION.md for the measurement.
    low_rating: float = 0.0
    # Quality prior: percentile, within the candidate pool, of a vote-count-
    # shrunk rating (quality_percentiles) — "rated well", not "widely known".
    # Off by default. Until hidden gems moved to catalog percentiles, the gem
    # bonus fired on almost every well-rated film and was this prior in
    # disguise; docs/EVALUATION.md measures what an explicit one recovers.
    quality: float = 0.0


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
# six Nolan films reads as "the algorithm found one name", not as taste, and a
# genre cap cannot see it (his films span action, war, drama and sci-fi).
# Applied to every director of a co-directed film.
DIRECTOR_CAP_RATIO = 0.1
MIN_DIRECTOR_CAP = 1


def director_cap_for(slate_size: int) -> int:
    """Per-director cap: 2 for a 20-card slate, scaled with slate size."""
    return max(MIN_DIRECTOR_CAP, round(slate_size * DIRECTOR_CAP_RATIO))


# Diversity: titles per collection (TMDb ``belongs_to_collection``, i.e. a
# franchise such as "The Matrix Collection"). One in twenty cards: the sequels
# of a film someone loved are the most obvious picks there are — they almost
# certainly know them already — so one card shows the franchise and the other
# slots go to discovery. Stricter than the director cap because sequels are
# near-duplicates; two films by one director are still two different films.
# Ingest stores ``extra["collection"] = {"id": <TMDb id>, "name": ...}``.
COLLECTION_KEY = "collection"
COLLECTION_CAP_RATIO = 0.05
MIN_COLLECTION_CAP = 1


def collection_cap_for(slate_size: int) -> int:
    """Per-collection cap: 1 for a 20-card slate, 2 for 40, scaled with slate size."""
    return max(MIN_COLLECTION_CAP, round(slate_size * COLLECTION_CAP_RATIO))


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
# TMDb numbers because TMDb popularity is a trending score whose scale drifts:
# in November 2026 its median was 2.5 and 99.8% of titles sat below 40, so the
# old absolute cut-off (popularity < 40) called Interstellar, Inception and
# The Shawshank Redemption "hidden gems".
CATALOG_PERCENTILES_KEY = "catalog_percentiles"

# Ratings from a handful of votes are noise; don't call those "hidden gems".
GEM_MIN_VOTES = 50
# ...and relative to the catalog: the bottom tenth by vote count is too thin to trust.
GEM_MIN_VOTE_PCT = 0.10
# A hidden gem is less known than most of the catalog on *both* popularity and
# vote count ("fame" = the higher of the two percentiles). Vote count matters:
# classics with a low trending score (The Silence of the Lambs) have huge vote
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
    falls back to the percentile *within these titles* — biased in
    production, where the pool is nearest neighbours plus the popular head;
    hence the stored values.
    """
    n = len(titles)
    popularity = percent_rank([float(t.popularity or 0.0) for t in titles])
    votes = np.full(n, np.nan, dtype=np.float64)
    known = [
        (i, float(v)) for i, t in enumerate(titles) if (v := getattr(t, "vote_count", None)) is not None
    ]
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


def quality_percentiles(vote_average: np.ndarray, vote_count: np.ndarray) -> np.ndarray:
    """0..1: share of these titles rated at or below each one, after shrinkage.

    A rating from a handful of votes is pulled toward the mean, with the median
    vote count as the prior's weight (the IMDb top-list formula), so ten votes
    of 9.0 do not beat ten thousand of 8.5. Unknown vote counts (NaN) count as
    the median. From ``florent/fervent-keller-0l0j9b``.
    """
    n = len(vote_average)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    counts = np.asarray(vote_count, dtype=np.float64)
    finite = counts[np.isfinite(counts)]
    prior_weight = max(float(np.median(finite)) if finite.size else 1.0, 1.0)
    counts = np.where(np.isfinite(counts), np.maximum(counts, 0.0), prior_weight)
    prior_mean = float(np.mean(vote_average))
    shrunk = (counts * vote_average + prior_weight * prior_mean) / (counts + prior_weight)
    return np.searchsorted(np.sort(shrunk), shrunk, side="right") / float(n)


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
                    f"A hidden gem: rated ★{vote_average:.1f}, "
                    "yet still under the radar"
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
                message="A deliberate stretch beyond your usual picks",
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
        # built, so label quality can be measured without explanations.
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


def collection_of(title: Any) -> int | None:
    """TMDb collection (franchise) id from ``extra``; None for standalone titles.

    Titles ingested before the field existed have no entry until they are
    re-ingested (``--refresh-existing``), so they are simply not capped.
    """
    raw = (getattr(title, "extra", None) or {}).get(COLLECTION_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None


def primary_genre(title: Any) -> str:
    """Main genre: the highest-weighted genre in the feature snapshot.

    The snapshot keeps TMDb's genre order (first genre weighs most); the ORM
    ``genres`` relationship lists genres by name, so it is only a fallback.
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
    among candidates ``allow`` accepts (quotas such as the genre, director and
    collection caps); ``on_pick`` is told about every pick so the quotas can
    count it. Vectorised: one matrix-vector product per pick instead of
    pairwise loops.
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
    """Per-slate caps: titles per primary genre, per director and per collection.

    Shared by core MMR, exploration and backfill, so no stage can undo
    another's diversity. A co-directed film counts for each director; a title
    outside any collection is never held back by the collection cap.
    """

    __slots__ = (
        "genres",
        "directors",
        "collections",
        "genre_cap",
        "director_cap",
        "collection_cap",
        "genre_counts",
        "director_counts",
        "collection_counts",
    )

    def __init__(
        self,
        genres: list[str],
        directors: list[tuple[str, ...]],
        collections: list[int | None],
        *,
        genre_cap: int,
        director_cap: int,
        collection_cap: int,
    ) -> None:
        self.genres = genres
        self.directors = directors
        self.collections = collections
        self.genre_cap = genre_cap
        self.director_cap = director_cap
        self.collection_cap = collection_cap
        self.genre_counts: Counter[str] = Counter()
        self.director_counts: Counter[str] = Counter()
        self.collection_counts: Counter[int] = Counter()

    def allows(self, i: int) -> bool:
        if self.genre_counts[self.genres[i]] >= self.genre_cap:
            return False
        collection = self.collections[i]
        if collection is not None and self.collection_counts[collection] >= self.collection_cap:
            return False
        return all(self.director_counts[d] < self.director_cap for d in self.directors[i])

    def add(self, i: int) -> None:
        self.genre_counts[self.genres[i]] += 1
        for director in self.directors[i]:
            self.director_counts[director] += 1
        collection = self.collections[i]
        if collection is not None:
            self.collection_counts[collection] += 1

    def relax(self, slate_size: int) -> None:
        """Raise one cap by one step, in the order the slate can best afford.

        Genre first (one more of a genre is the mildest repetition), then
        director, then collection (another sequel is the most repetitive card
        there is). A cap at ``slate_size`` can no longer bind, so the next one
        is only raised once the earlier ones are out of the way.
        """
        if self.genre_cap < slate_size:
            self.genre_cap += 1
        elif self.director_cap < slate_size:
            self.director_cap += 1
        else:
            self.collection_cap += 1


def _interleave(main: list[int], extra: list[int]) -> list[int]:
    """Spread ``extra`` evenly through ``main`` (never at position 0)."""
    if not extra:
        return list(main)
    out = list(main)
    step = max(len(out) // (len(extra) + 1), 1)
    for n, item in enumerate(extra, start=1):
        out.insert(min(n * step + (n - 1), len(out)), item)
    return out


@dataclass(slots=True)
class CandidateChannels:
    """Per-candidate scoring inputs for one user, before any weights apply.

    Weight-independent, so one computation serves every weighting of the same
    pool: offline evaluation sweeps weights without re-scoring the sparse
    overlap, which is the expensive part.
    """

    pool: list[Any]
    unit: np.ndarray  # L2-normalised content vectors, one row per candidate
    similarity: np.ndarray  # cosine to the user's taste vector
    positive: np.ndarray  # sparse overlap with liked features
    negative: np.ndarray  # sparse overlap with disliked features
    gems: np.ndarray  # hidden-gem bonus (catalog percentiles; see gem_boost)
    popularity: np.ndarray
    fame: np.ndarray  # 0..1 catalog percentile: how well known (cold-start prior)
    low_rated: np.ndarray  # 0..1 low_rating_penalty
    vote_average: np.ndarray
    vote_count: np.ndarray  # NaN where unknown
    genres: list[str]  # primary genre per candidate
    directors: list[tuple[str, ...]]  # per candidate, for the director cap
    collections: list[int | None]  # TMDb collection per candidate, for the franchise cap
    cold: bool  # thin profile: the cold-start popularity prior applies

    def subset(self, indices: Sequence[int]) -> CandidateChannels:
        """The same channels restricted to ``indices`` (in that order).

        Catalog percentiles (gems, fame) are not recomputed: they describe
        the catalog, not the subset — as in production.
        """
        idx = np.asarray(indices, dtype=np.int64)
        return CandidateChannels(
            pool=[self.pool[i] for i in idx],
            unit=self.unit[idx],
            similarity=self.similarity[idx],
            positive=self.positive[idx],
            negative=self.negative[idx],
            gems=self.gems[idx],
            popularity=self.popularity[idx],
            fame=self.fame[idx],
            low_rated=self.low_rated[idx],
            vote_average=self.vote_average[idx],
            vote_count=self.vote_count[idx],
            genres=[self.genres[i] for i in idx],
            directors=[self.directors[i] for i in idx],
            collections=[self.collections[i] for i in idx],
            cold=self.cold,
        )


def candidate_channels(
    *,
    user_vector: Sequence[float] | None,
    user_features: dict[str, Any],
    titles: list[Any],
    exclude_ids: set[UUID],
) -> CandidateChannels | None:
    """Score inputs for every rankable candidate; None when nothing is rankable.

    Candidates are de-duplicated, excluded ids and unembedded titles dropped.
    ``user_features`` may still carry the explain-memory block; it is ignored.
    """
    scoring_features, _memory = strip_explain_memory(user_features)
    pool: list[Any] = []
    seen_ids: set[UUID] = set()
    for title in titles:
        if title.id in exclude_ids or title.id in seen_ids or title.embedding is None:
            continue
        seen_ids.add(title.id)
        pool.append(title)
    if not pool:
        return None
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

    # Hidden gems, the cold-start prior and the low-rating penalty read where a
    # title sits in the whole catalog (stored by ingest), not raw TMDb numbers.
    popularity_pct, vote_count_pct = catalog_percentiles(pool)
    vote_average = np.asarray([float(t.vote_average or 0.0) for t in pool], dtype=np.float64)
    raw_votes = [getattr(t, "vote_count", None) for t in pool]
    vote_count = np.asarray(
        [np.nan if v is None else float(v) for v in raw_votes], dtype=np.float64
    )
    gems = np.asarray(
        [
            gem_boost(
                float(vote_average[i]),
                popularity_pct=float(popularity_pct[i]),
                vote_count_pct=float(vote_count_pct[i]),
                vote_count=raw_votes[i],
            )
            for i in range(n)
        ],
        dtype=np.float64,
    )
    low_rated = np.asarray(
        [
            low_rating_penalty(
                float(vote_average[i]),
                vote_count=raw_votes[i],
                vote_count_pct=float(vote_count_pct[i]),
            )
            for i in range(n)
        ],
        dtype=np.float64,
    )

    return CandidateChannels(
        pool=pool,
        unit=unit,
        similarity=similarity,
        positive=np.asarray([p for p, _ in sparse], dtype=np.float64),
        negative=np.asarray([q for _, q in sparse], dtype=np.float64),
        gems=gems,
        popularity=np.asarray([float(t.popularity) for t in pool], dtype=np.float64),
        fame=np.fmax(popularity_pct, vote_count_pct),  # fame(); a NaN vote pct is ignored
        low_rated=low_rated,
        vote_average=vote_average,
        vote_count=vote_count,
        genres=[primary_genre(t) for t in pool],
        directors=[directors_of(t) for t in pool],
        collections=[collection_of(t) for t in pool],
        cold=cold,
    )


def blend_scores(channels: CandidateChannels, weights: RankingWeights = DEFAULT_WEIGHTS) -> np.ndarray:
    """Score = weighted dense similarity + sparse overlap − sparse penalty
    + hidden-gem bonus + popularity prior (cold start, or warm when enabled)
    − low-rating penalty + quality prior (both off by default)."""
    n = len(channels.pool)
    popularity = channels.popularity
    prior = COLD_PRIOR_MAX * channels.fame if channels.cold else np.zeros(n)
    warm_prior = np.zeros(n)
    if not channels.cold and weights.warm_popularity and n > 1:
        # Rank-based percentile: 1.0 for the most popular candidate, 0.0 for the least.
        warm_prior = np.argsort(np.argsort(popularity, kind="stable"), kind="stable") / (n - 1)
    # Pool-relative like warm_prior, so it is computed here, after any subset.
    quality = (
        quality_percentiles(channels.vote_average, channels.vote_count)
        if weights.quality
        else np.zeros(n)
    )
    return (
        weights.similarity * channels.similarity
        + weights.sparse_positive * channels.positive
        - weights.sparse_negative * channels.negative
        + weights.hidden_gem * channels.gems
        + weights.cold_popularity * prior
        + weights.warm_popularity * warm_prior
        - weights.low_rating * channels.low_rated
        + weights.quality * quality
    )


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
    max_per_collection: int | None = None,
    with_reasons: bool = True,
) -> list[RankedItem]:
    """Rank candidate titles for one user.

    1. Score (``candidate_channels`` → ``blend_scores``) = weighted dense
       similarity + sparse overlap − sparse penalty + hidden-gem bonus
       (catalog percentiles) + popularity prior (cold start only).
    2. MMR over the top of the list, with per-primary-genre, per-director and
       per-collection (franchise) caps.
    3. Exploration: well-rated, moderately relevant titles from genres the slate
       doesn't cover yet, spread through the slate, under the same caps.
    4. Backfill by score, still honouring every cap; if the catalog can't fill
       the slate that way, the genre cap is raised one step at a time, the
       director cap only once the genre cap no longer binds, and the
       collection cap last of all.

    Steps 2–4 are ``select_slate``.
    """
    if slate_size <= 0:
        return []
    channels = candidate_channels(
        user_vector=user_vector,
        user_features=user_features,
        titles=titles,
        exclude_ids=exclude_ids,
    )
    if channels is None:
        return []
    return select_slate(
        channels,
        blend_scores(channels, weights),
        user_features=user_features,
        slate_size=slate_size,
        mmr_lambda=mmr_lambda,
        exploration_slots=exploration_slots,
        explain_memory=explain_memory,
        max_per_genre=max_per_genre,
        max_per_director=max_per_director,
        max_per_collection=max_per_collection,
        with_reasons=with_reasons,
    )


def select_slate(
    channels: CandidateChannels,
    scores: np.ndarray,
    *,
    user_features: dict[str, Any],
    slate_size: int,
    mmr_lambda: float,
    exploration_slots: int = 3,
    explain_memory: dict[str, Any] | None = None,
    max_per_genre: int | None = None,
    max_per_director: int | None = None,
    max_per_collection: int | None = None,
    with_reasons: bool = True,
) -> list[RankedItem]:
    """Steps 2–4 of ``rank_titles`` over already-scored candidates.

    Every stage (MMR, exploration, backfill) draws from one set of per-slate
    quotas — genre, director, collection — so no stage can undo another's
    diversity.
    """
    if slate_size <= 0 or not channels.pool:
        return []
    if max_per_genre is None:
        max_per_genre = genre_cap_for(slate_size)
    if max_per_director is None:
        max_per_director = director_cap_for(slate_size)
    if max_per_collection is None:
        max_per_collection = collection_cap_for(slate_size)
    scoring_features, memory_from_features = strip_explain_memory(user_features)
    memory = explain_memory if explain_memory is not None else memory_from_features

    pool = channels.pool
    n = len(pool)
    unit = channels.unit
    similarity = channels.similarity
    gems = channels.gems
    genres = channels.genres
    order = [int(i) for i in np.argsort(-scores, kind="stable")]

    quotas = _SlateQuotas(
        genres,
        channels.directors,
        channels.collections,
        genre_cap=max_per_genre,
        director_cap=max_per_director,
        collection_cap=max_per_collection,
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

    # Backfill by score under every cap. If the slate is still short, raise the
    # genre cap one step at a time, so no genre gets another slot before every
    # genre with candidates left has had its turn. The director cap is raised
    # only once the genre cap can no longer bind, and the collection cap after
    # that: a third film by one director, or a second sequel, is the last
    # resort, not a side effect of filling a genre.
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
        quotas.relax(slate_size)

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
