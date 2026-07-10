from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.recommendation.embeddings import cosine, top_feature_overlap


@dataclass
class Reason:
    code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class RankedItem:
    title_id: UUID
    score: float
    reasons: list[Reason]


def _feature_snapshot(title_extra: dict[str, Any] | None) -> dict[str, float]:
    snap = (title_extra or {}).get("feature_snapshot") or {}
    return {str(k): float(v) for k, v in snap.items()}


def explain(
    *,
    user_features: dict[str, Any],
    title_extra: dict[str, Any] | None,
    title_genres: list[str],
    similarity: float,
) -> list[Reason]:
    reasons: list[Reason] = []
    title_features = _feature_snapshot(title_extra)
    overlap = top_feature_overlap(user_features, title_features, limit=8)

    genre_hits = [k.split(":", 1)[1] for k, _ in overlap if k.startswith("genre:")]
    if genre_hits:
        pretty = ", ".join(g.title() for g in genre_hits[:3])
        reasons.append(
            Reason(
                code="shared_genre",
                message=f"Matches genres you like: {pretty}",
                evidence={"genres": genre_hits[:3]},
            )
        )

    directors = [
        k.split(":", 2)[-1]
        for k, _ in overlap
        if k.startswith("person:director:")
    ]
    if directors:
        name = directors[0].title()
        reasons.append(
            Reason(
                code="same_director",
                message=f"Connected to director taste ({name})",
                evidence={"directors": directors[:2]},
            )
        )

    cast_hits = [k.split(":", 2)[-1] for k, _ in overlap if k.startswith("person:cast:")]
    if cast_hits:
        reasons.append(
            Reason(
                code="similar_cast",
                message=f"Features talent you respond to ({cast_hits[0].title()})",
                evidence={"cast": cast_hits[:3]},
            )
        )

    keywords = [k.split(":", 1)[1] for k, _ in overlap if k.startswith("kw:")]
    if keywords:
        themes = ", ".join(keywords[:3])
        reasons.append(
            Reason(
                code="similar_themes",
                message=f"Similar themes: {themes}",
                evidence={"keywords": keywords[:3]},
            )
        )

    decades = [k.split(":", 1)[1] for k, _ in overlap if k.startswith("decade:")]
    if decades:
        reasons.append(
            Reason(
                code="similar_era",
                message=f"Fits your preferred era ({decades[0]}s)",
                evidence={"decades": decades[:1]},
            )
        )

    if not reasons and title_genres:
        reasons.append(
            Reason(
                code="genre_fit",
                message=f"In your orbit: {', '.join(title_genres[:2])}",
                evidence={"genres": title_genres[:2]},
            )
        )

    if similarity >= 0.55 and not any(r.code == "similar_themes" for r in reasons):
        reasons.append(
            Reason(
                code="taste_similarity",
                message="Strong overall match to your taste profile",
                evidence={"similarity": round(similarity, 3)},
            )
        )

    if not reasons:
        reasons.append(
            Reason(
                code="discovery",
                message="A discovery pick to broaden your watchlist",
                evidence={},
            )
        )

    return reasons[:3]


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
) -> list[RankedItem]:
    """titles: objects with id, embedding, extra, genres, popularity, vote_average."""
    scored: list[tuple[Any, float]] = []

    for title in titles:
        if title.id in exclude_ids:
            continue
        if title.embedding is None:
            continue
        emb = list(title.embedding)

        sim = cosine(user_vector, emb) if user_vector is not None else 0.0
        title_features = _feature_snapshot(title.extra)
        feature_boost = 0.0
        for key, t_w in title_features.items():
            u_w = float(user_features.get(key, 0.0) or 0.0)
            if u_w > 0:
                feature_boost += min(u_w, 3.0) * 0.03 * t_w

        # Hidden gem: solid rating, not ultra-popular
        gem = 0.0
        if title.vote_average >= 7.2 and title.popularity < 40:
            gem = 0.08
        elif title.vote_average >= 7.5 and title.popularity < 80:
            gem = 0.04

        # Mild popularity prior for cold profiles
        pop_prior = 0.0
        if not user_vector:
            pop_prior = min(title.popularity / 200.0, 0.25)

        score = sim + feature_boost + gem + pop_prior
        scored.append((title, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    head = scored[: max(slate_size * 4, 40)]

    # Reserve exploration from slightly lower ranks with good quality
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

    # Soft genre cap
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

    # Fill if under-sized
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
        reasons = explain(
            user_features=user_features,
            title_extra=title.extra,
            title_genres=genre_names,
            similarity=sim,
        )
        results.append(RankedItem(title_id=tid, score=score, reasons=reasons))

    return results
