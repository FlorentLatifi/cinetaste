"""Deterministic content embeddings from structured title features.

No external model dependency for MVP. Re-embed in batch when upgrading to
sentence-transformers or a hosted embedding API.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Any

from app.infrastructure.db.models.catalog import EMBEDDING_DIM

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stable_index(key: str, dim: int = EMBEDDING_DIM) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % dim


def _add(vec: list[float], key: str, weight: float = 1.0) -> None:
    idx = _stable_index(key)
    # Split positive/negative polarity with a second hash bit for denser use of dims
    sign = 1.0 if (int.from_bytes(hashlib.md5(key.encode()).digest()[:1], "big") % 2 == 0) else -1.0
    vec[idx] += weight * sign


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm < 1e-12:
        return vec
    return [v / norm for v in vec]


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def build_title_embedding(
    *,
    name: str,
    overview: str | None,
    genres: Iterable[str],
    keywords: Iterable[str],
    people: Iterable[str],
    media_type: str,
    release_year: int | None,
    runtime: int | None,
    popularity: float,
    vote_average: float,
) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM

    _add(vec, f"media:{media_type}", 1.5)
    for g in genres:
        _add(vec, f"genre:{g.lower()}", 2.0)
    for k in keywords:
        _add(vec, f"kw:{k.lower()}", 1.2)
    for p in people:
        _add(vec, f"person:{p.lower()}", 1.4)

    if release_year:
        decade = (release_year // 10) * 10
        _add(vec, f"decade:{decade}", 1.0)
        _add(vec, f"year:{release_year}", 0.3)

    if runtime is not None:
        if runtime < 90:
            bucket = "short"
        elif runtime < 120:
            bucket = "medium"
        elif runtime < 150:
            bucket = "long"
        else:
            bucket = "epic"
        _add(vec, f"runtime:{bucket}", 0.8)

    # Soft popularity / quality signals (secondary to taste)
    pop_bucket = min(int(popularity // 20), 10)
    _add(vec, f"pop:{pop_bucket}", 0.4)
    rating_bucket = int(round(vote_average))
    _add(vec, f"rating:{rating_bucket}", 0.5)

    for token in tokenize(name)[:12]:
        _add(vec, f"title:{token}", 0.6)
    for token in tokenize(overview)[:40]:
        _add(vec, f"plot:{token}", 0.35)

    return _l2_normalize(vec)


def features_from_title(
    *,
    genres: Iterable[str],
    keywords: Iterable[str],
    people: Iterable[tuple[str, str]],
    release_year: int | None,
    runtime: int | None,
    media_type: str,
) -> dict[str, float]:
    """Sparse interpretable features used for explanations and taste updates."""
    features: dict[str, float] = {f"media:{media_type}": 1.0}
    for g in genres:
        features[f"genre:{g.lower()}"] = features.get(f"genre:{g.lower()}", 0.0) + 1.0
    for k in list(keywords)[:15]:
        features[f"kw:{k.lower()}"] = features.get(f"kw:{k.lower()}", 0.0) + 0.8
    for person_name, role in people:
        key = f"person:{role}:{person_name.lower()}"
        features[key] = features.get(key, 0.0) + 1.0
    if release_year:
        features[f"decade:{(release_year // 10) * 10}"] = 1.0
    if runtime is not None:
        if runtime < 90:
            features["runtime:short"] = 1.0
        elif runtime < 120:
            features["runtime:medium"] = 1.0
        elif runtime < 150:
            features["runtime:long"] = 1.0
        else:
            features["runtime:epic"] = 1.0
    return features


def blend_vectors(
    vectors: list[tuple[list[float], float]],
) -> list[float] | None:
    if not vectors:
        return None
    acc = [0.0] * EMBEDDING_DIM
    total_w = 0.0
    for vec, weight in vectors:
        if not vec:
            continue
        total_w += abs(weight)
        for i, v in enumerate(vec):
            acc[i] += v * weight
    if total_w < 1e-12:
        return None
    return _l2_normalize(acc)


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def top_feature_overlap(
    user_features: dict[str, Any],
    title_features: dict[str, float],
    *,
    limit: int = 5,
) -> list[tuple[str, float]]:
    scored: list[tuple[str, float]] = []
    for key, t_w in title_features.items():
        u_w = float(user_features.get(key, 0.0) or 0.0)
        if u_w > 0 and t_w > 0:
            scored.append((key, u_w * t_w))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
