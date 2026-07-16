from __future__ import annotations

from typing import Any
from uuid import UUID

from app.recommendation.embeddings import cosine, sparse_channel_scores
from app.recommendation.explanations import (
    Reason,
    build_reasons,
    strip_explain_memory,
)

# Re-export for callers/tests that import Reason from pipeline.
__all__ = ["Reason", "RankedItem", "explain", "mmr_select", "rank_titles"]


# Blend of dense similarity vs sparse explainable features.
W_SIM = 0.42
W_POS = 0.40
W_NEG = 0.12
W_GEM = 1.0
W_COLD = 1.0


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


def mmr_select(
    candidates: list[tuple[UUID, float, list[float] | None]],
    *,
    k: int,
    lambda_mult: float = 0.7,
) -> list[UUID]:
    """Maximal Marginal Relevance for diversity."""
    selected: list[UUID] = []
    selected_vecs: list[list[float] | None] = []
    remaining = candidates[:]

    while remaining and len(selected) < k:
        best_idx = 0
        best_score = float("-inf")
        for i, (_tid, rel, vec) in enumerate(remaining):
            if not selected:
                mmr = rel
            else:
                max_sim = 0.0
                for svec in selected_vecs:
                    max_sim = max(max_sim, cosine(vec, svec))
                mmr = lambda_mult * rel - (1.0 - lambda_mult) * max_sim
            if mmr > best_score:
                best_score = mmr
                best_idx = i
        tid, rel, vec = remaining.pop(best_idx)
        selected.append(tid)
        selected_vecs.append(vec)

    return selected


def rank_titles(
    *,
    user_vector: list[float] | None,
    user_features: dict[str, Any],
    titles: list[Any],
    exclude_ids: set[UUID],
    slate_size: int,
    mmr_lambda: float,
    exploration_slots: int = 3,
    explain_memory: dict[str, Any] | None = None,
) -> list[RankedItem]:
    """titles: objects with id, embedding, extra, genres, popularity, vote_average, name."""
    scoring_features, memory_from_features = strip_explain_memory(user_features)
    memory = explain_memory if explain_memory is not None else memory_from_features

    scored: list[tuple[Any, float]] = []
    profile_strength = sum(abs(v) for v in scoring_features.values()) if scoring_features else 0.0
    cold = user_vector is None or profile_strength < 0.8

    for title in titles:
        if title.id in exclude_ids:
            continue
        if title.embedding is None:
            continue
        emb = list(title.embedding)

        sim = cosine(user_vector, emb) if user_vector is not None else 0.0
        title_features = _feature_snapshot(title.extra)
        pos_sparse, neg_sparse = sparse_channel_scores(scoring_features, title_features)

        gem = 0.0
        if title.vote_average >= 7.2 and title.popularity < 40:
            gem = 0.08
        elif title.vote_average >= 7.5 and title.popularity < 80:
            gem = 0.04

        pop_prior = 0.0
        if cold:
            pop_prior = min(title.popularity / 200.0, 0.22)

        score = (
            W_SIM * sim
            + W_POS * pos_sparse
            - W_NEG * neg_sparse
            + W_GEM * gem
            + W_COLD * pop_prior
        )
        scored.append((title, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    head = scored[: max(slate_size * 4, 40)]

    exploration_pool = [
        t
        for t, s in scored[slate_size : slate_size * 5]
        if t.vote_average >= 6.8
    ][: exploration_slots * 3]

    mmr_input = [
        (t.id, s, list(t.embedding) if t.embedding is not None else None)  # noqa: SIM223
        for t, s in head
    ]
    core_k = max(slate_size - exploration_slots, 1)
    selected_ids = mmr_select(mmr_input, k=core_k, lambda_mult=mmr_lambda)

    by_id = {t.id: (t, s) for t, s in scored}
    genre_counts: dict[str, int] = {}
    final_ids: list[UUID] = []
    for tid in selected_ids:
        title, _ = by_id[tid]
        primary = title.genres[0].name.lower() if title.genres else "unknown"
        if genre_counts.get(primary, 0) >= 4:
            continue
        genre_counts[primary] = genre_counts.get(primary, 0) + 1
        final_ids.append(tid)

    for title in exploration_pool:
        if title.id in final_ids or title.id in exclude_ids:
            continue
        final_ids.append(title.id)
        if len(final_ids) >= slate_size:
            break

    for t, _ in scored:
        if len(final_ids) >= slate_size:
            break
        if t.id not in final_ids and t.id not in exclude_ids:
            final_ids.append(t.id)

    results: list[RankedItem] = []
    for tid in final_ids[:slate_size]:
        title, score = by_id[tid]
        emb = list(title.embedding) if title.embedding is not None else None
        sim = cosine(user_vector, emb) if user_vector is not None and emb is not None else 0.0
        genre_names = [g.name for g in title.genres]
        reasons = build_reasons(
            user_features=scoring_features,
            explain_memory=memory,
            title_name=getattr(title, "name", None),
            title_extra=title.extra,
            title_genres=genre_names,
            similarity=sim,
        )
        results.append(RankedItem(title_id=tid, score=score, reasons=reasons))

    return results
