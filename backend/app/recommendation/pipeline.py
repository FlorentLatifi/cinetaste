from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.recommendation.embeddings import (
    cosine,
    sparse_channel_scores,
    top_feature_overlap,
)


# Blend of dense similarity vs sparse explainable features.
W_SIM = 0.42
W_POS = 0.40
W_NEG = 0.12
W_GEM = 1.0
W_COLD = 1.0


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


def _pretty_person(raw: str) -> str:
    return " ".join(part.capitalize() for part in raw.replace("_", " ").split())


def _pretty_label(raw: str) -> str:
    return raw.replace("_", " ").strip()


def explain(
    *,
    user_features: dict[str, Any],
    title_extra: dict[str, Any] | None,
    title_genres: list[str],
    similarity: float,
) -> list[Reason]:
    """Build up to 3 human reasons, preferring people/tone/themes over genre."""
    reasons: list[Reason] = []
    title_features = _feature_snapshot(title_extra)
    # Pull a wider overlap so non-genre signals can surface first.
    overlap = top_feature_overlap(user_features, title_features, limit=16)

    def _take(prefix: str, n: int = 3) -> list[str]:
        hits: list[str] = []
        for key, strength in overlap:
            if strength <= 0:
                continue
            if not key.startswith(prefix):
                continue
            value = key[len(prefix) :]
            if value and value not in hits:
                hits.append(value)
            if len(hits) >= n:
                break
        return hits

    directors = _take("person:director:", 2)
    if directors:
        name = _pretty_person(directors[0])
        reasons.append(
            Reason(
                code="same_director",
                message=f"You tend to like films by {name}",
                evidence={"directors": directors[:2]},
            )
        )

    cast_hits = _take("person:cast:", 3)
    if cast_hits and len(reasons) < 3:
        name = _pretty_person(cast_hits[0])
        reasons.append(
            Reason(
                code="similar_cast",
                message=f"Features talent you respond to ({name})",
                evidence={"cast": cast_hits[:3]},
            )
        )

    tones = _take("tone:", 3)
    if tones and len(reasons) < 3:
        pretty = ", ".join(_pretty_label(t) for t in tones[:2])
        reasons.append(
            Reason(
                code="similar_tone",
                message=f"Similar tone to titles you like ({pretty})",
                evidence={"tones": tones[:3]},
            )
        )

    keywords = _take("kw:", 4)
    if keywords and len(reasons) < 3:
        themes = ", ".join(_pretty_label(k) for k in keywords[:3])
        reasons.append(
            Reason(
                code="similar_themes",
                message=f"Shares themes you like: {themes}",
                evidence={"keywords": keywords[:3]},
            )
        )

    writers = _take("person:writer:", 2)
    if writers and len(reasons) < 3:
        name = _pretty_person(writers[0])
        reasons.append(
            Reason(
                code="same_writer",
                message=f"Connected to writers you favor ({name})",
                evidence={"writers": writers[:2]},
            )
        )

    countries = _take("country:", 2)
    if countries and len(reasons) < 3:
        reasons.append(
            Reason(
                code="similar_origin",
                message=f"From cinema origins you lean toward ({', '.join(countries)})",
                evidence={"countries": countries},
            )
        )

    langs = _take("lang:", 2)
    if langs and len(reasons) < 3:
        reasons.append(
            Reason(
                code="similar_language",
                message=f"Matches languages you enjoy ({', '.join(langs)})",
                evidence={"languages": langs},
            )
        )

    decades = _take("decade:", 1)
    if decades and len(reasons) < 3:
        reasons.append(
            Reason(
                code="similar_era",
                message=f"Fits your preferred era ({decades[0]}s)",
                evidence={"decades": decades[:1]},
            )
        )

    genre_hits = _take("genre:", 3)
    if genre_hits and len(reasons) < 3:
        pretty = ", ".join(g.title() for g in genre_hits[:3])
        reasons.append(
            Reason(
                code="shared_genre",
                message=f"Matches genres you like: {pretty}",
                evidence={"genres": genre_hits[:3]},
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

    if similarity >= 0.52 and len(reasons) < 3:
        if not any(r.code == "taste_similarity" for r in reasons):
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
    profile_strength = sum(abs(float(v)) for v in user_features.values()) if user_features else 0.0
    cold = user_vector is None or profile_strength < 0.8

    for title in titles:
        if title.id in exclude_ids:
            continue
        if title.embedding is None:
            continue
        emb = list(title.embedding)

        sim = cosine(user_vector, emb) if user_vector is not None else 0.0
        title_features = _feature_snapshot(title.extra)
        pos_sparse, neg_sparse = sparse_channel_scores(user_features, title_features)

        # Hidden gem: solid rating, not ultra-popular
        gem = 0.0
        if title.vote_average >= 7.2 and title.popularity < 40:
            gem = 0.08
        elif title.vote_average >= 7.5 and title.popularity < 80:
            gem = 0.04

        # Mild popularity prior for cold profiles
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
