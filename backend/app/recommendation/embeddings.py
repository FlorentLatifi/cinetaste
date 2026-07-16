"""Deterministic content embeddings + sparse feature snapshots.

No external model dependency for MVP. Re-embed in batch when upgrading to
sentence-transformers or a hosted embedding API.

Feature schema (FEATURE_SCHEMA_VERSION)
---------------------------------------
Sparse keys used for taste learning and explainable ranking:

| Prefix              | Meaning                         | Typical title weight        |
|---------------------|---------------------------------|-----------------------------|
| genre:              | TMDb genres                     | 1.0 primary, 0.7 secondary  |
| kw:                 | TMDb keywords/tags              | 0.9 (capped)                |
| person:director:    | Director                        | 2.2                         |
| person:writer:      | Writer / screenplay / creator   | 1.2                         |
| person:cast:        | Cast by billing order           | 1.5 → 0.4                   |
| decade:             | Release decade                  | 0.9                         |
| lang:               | Original language (ISO 639-1)   | 0.85                        |
| country:            | Production country (ISO 3166-1) | 0.8                         |
| tone:               | Derived mood tags from keywords | 0.7–1.0                     |
| runtime:            | short/medium/long/epic          | 0.5                         |
| media:              | movie | tv                      | 0.6                         |

Bump FEATURE_SCHEMA_VERSION when keys/weights change so re-ingest can detect
stale snapshots.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.infrastructure.db.models.catalog import EMBEDDING_DIM

FEATURE_SCHEMA_VERSION = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Ultra-generic TMDb keywords that add noise more than signal.
_KEYWORD_NOISE = frozenset(
    {
        "based on novel or book",
        "based on novel",
        "duringcreditsstinger",
        "aftercreditsstinger",
        "woman director",
        "parent child relationship",
        "friendship",
        "murder",  # too broad; keep specific murder-* variants
        "love",
        "death",
        "violence",
        "sequel",
        "remake",
    }
)

# Keyword (substring or exact, lowercased) → tone tag for explainable mood.
# First match wins per keyword; multiple tones can attach to one title.
_KEYWORD_TONE_RULES: list[tuple[str, str, float]] = [
    ("neo-noir", "dark", 1.0),
    ("film noir", "dark", 1.0),
    ("noir", "dark", 0.85),
    ("cyberpunk", "dark", 0.9),
    ("dystopia", "bleak", 1.0),
    ("post-apocalyptic", "bleak", 0.95),
    ("feel-good", "uplifting", 1.0),
    ("coming of age", "coming_of_age", 1.0),
    ("bittersweet", "bittersweet", 1.0),
    ("melancholy", "melancholy", 1.0),
    ("tragic", "tragic", 0.9),
    ("whimsical", "whimsical", 1.0),
    ("surreal", "surreal", 1.0),
    ("mind-bending", "cerebral", 1.0),
    ("time travel", "cerebral", 0.85),
    ("philosophical", "cerebral", 0.9),
    ("tense", "tense", 0.9),
    ("suspense", "tense", 0.8),
    ("psychological thriller", "tense", 1.0),
    ("horror", "horror", 0.75),
    ("found footage", "horror", 0.85),
    ("gore", "horror", 0.7),
    ("slapstick", "comedic", 0.9),
    ("satire", "satirical", 1.0),
    ("dark comedy", "satirical", 0.95),
    ("romantic", "romantic", 0.75),
    ("love story", "romantic", 0.85),
    ("epic", "epic", 0.85),
    ("war", "war", 0.7),
    ("heist", "stylish", 0.9),
    ("space opera", "epic", 0.9),
    ("anime", "anime", 0.8),
    ("martial arts", "action_heavy", 0.85),
    ("slow burn", "meditative", 0.95),
    ("atmospheric", "meditative", 0.8),
]

# Family multipliers for sparse scoring / family normalization.
FAMILY_SCORE_WEIGHTS: dict[str, float] = {
    "person:director:": 1.45,
    "person:writer:": 0.95,
    "person:cast:": 1.15,
    "tone:": 1.2,
    "kw:": 1.05,
    "country:": 0.9,
    "lang:": 0.85,
    "genre:": 0.72,
    "decade:": 0.65,
    "runtime:": 0.4,
    "media:": 0.35,
}

# Target L2 mass per family after taste accumulation (prevents keyword flood).
_FAMILY_L2_TARGETS: dict[str, float] = {
    "person:director:": 2.5,
    "person:writer:": 1.8,
    "person:cast:": 2.8,
    "tone:": 2.0,
    "kw:": 2.5,
    "country:": 1.5,
    "lang:": 1.5,
    "genre:": 2.8,
    "decade:": 1.5,
    "runtime:": 1.0,
    "media:": 1.0,
}


@dataclass(frozen=True, slots=True)
class PersonSignal:
    """One cast/crew contribution with importance for features + embeddings."""

    name: str
    role: str  # director | writer | cast
    billing_order: int | None = None

    def feature_key(self) -> str:
        role = self.role.strip().lower()
        if role not in {"director", "writer", "cast"}:
            role = "cast"
        return f"person:{role}:{self.name.strip().lower()}"

    def feature_weight(self) -> float:
        role = self.role.strip().lower()
        if role == "director":
            return 2.2
        if role == "writer":
            return 1.2
        # Cast: billing order 0 is lead
        order = 99 if self.billing_order is None else int(self.billing_order)
        if order <= 1:
            return 1.5
        if order <= 3:
            return 1.1
        if order <= 5:
            return 0.75
        return 0.4

    def embed_weight(self) -> float:
        # Embeddings use slightly softer spread so one star doesn't dominate dims.
        return self.feature_weight() * 0.85


def _stable_index(key: str, dim: int = EMBEDDING_DIM) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % dim


def _add(vec: list[float], key: str, weight: float = 1.0) -> None:
    idx = _stable_index(key)
    sign = (
        1.0
        if (int.from_bytes(hashlib.md5(key.encode()).digest()[:1], "big") % 2 == 0)
        else -1.0
    )
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


def normalize_person_signals(
    people: Iterable[PersonSignal | tuple[str, str] | tuple[str, str, int | None] | str],
) -> list[PersonSignal]:
    """Accept PersonSignal, (name, role), (name, role, order), or bare name (cast)."""
    out: list[PersonSignal] = []
    for item in people:
        if isinstance(item, PersonSignal):
            out.append(item)
        elif isinstance(item, str):
            out.append(PersonSignal(name=item, role="cast", billing_order=None))
        elif isinstance(item, tuple):
            if len(item) == 2:
                out.append(PersonSignal(name=str(item[0]), role=str(item[1]), billing_order=None))
            elif len(item) >= 3:
                order = item[2]
                out.append(
                    PersonSignal(
                        name=str(item[0]),
                        role=str(item[1]),
                        billing_order=None if order is None else int(order),
                    )
                )
    return out


def filter_keywords(keywords: Iterable[str], *, limit: int = 20) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in keywords:
        name = str(raw).strip().lower()
        if not name or name in seen or name in _KEYWORD_NOISE:
            continue
        seen.add(name)
        cleaned.append(name)
        if len(cleaned) >= limit:
            break
    return cleaned


def tones_from_keywords(keywords: Iterable[str]) -> dict[str, float]:
    """Map keywords → tone tags with weights (max per tone kept)."""
    tones: dict[str, float] = {}
    for kw in keywords:
        low = str(kw).strip().lower()
        for needle, tone, weight in _KEYWORD_TONE_RULES:
            if needle in low:
                prev = tones.get(tone, 0.0)
                if weight > prev:
                    tones[tone] = weight
                break
    return tones


def feature_family(key: str) -> str:
    """Return the longest matching family prefix for a feature key."""
    best = ""
    for prefix in FAMILY_SCORE_WEIGHTS:
        if key.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return best or "other:"


def family_score_weight(key: str) -> float:
    fam = feature_family(key)
    return FAMILY_SCORE_WEIGHTS.get(fam, 0.5)


def normalize_feature_families(features: dict[str, float]) -> dict[str, float]:
    """Scale each feature family so one channel cannot drown the profile."""
    buckets: dict[str, list[tuple[str, float]]] = {}
    for key, value in features.items():
        fam = feature_family(key)
        buckets.setdefault(fam, []).append((key, float(value)))

    out: dict[str, float] = {}
    for fam, items in buckets.items():
        target = _FAMILY_L2_TARGETS.get(fam, 2.0)
        l2 = math.sqrt(sum(v * v for _, v in items))
        scale = 1.0 if l2 < 1e-9 or l2 <= target else target / l2
        for key, value in items:
            scaled = value * scale
            if abs(scaled) > 0.05:
                out[key] = round(scaled, 4)
    return out


def features_from_title(
    *,
    genres: Iterable[str],
    keywords: Iterable[str],
    people: Iterable[PersonSignal | tuple[str, str] | tuple[str, str, int | None] | str],
    release_year: int | None,
    runtime: int | None,
    media_type: str,
    original_language: str | None = None,
    countries: Iterable[str] | None = None,
) -> dict[str, float]:
    """Sparse interpretable features used for explanations and taste updates."""
    features: dict[str, float] = {f"media:{media_type}": 0.6}

    genre_list = [str(g).strip() for g in genres if str(g).strip()]
    for i, g in enumerate(genre_list):
        w = 1.0 if i == 0 else 0.7
        key = f"genre:{g.lower()}"
        features[key] = features.get(key, 0.0) + w

    kw_list = filter_keywords(keywords, limit=20)
    for k in kw_list:
        key = f"kw:{k}"
        features[key] = features.get(key, 0.0) + 0.9

    for person in normalize_person_signals(people):
        if not person.name.strip():
            continue
        key = person.feature_key()
        features[key] = features.get(key, 0.0) + person.feature_weight()

    if release_year:
        features[f"decade:{(release_year // 10) * 10}"] = 0.9

    if runtime is not None:
        if runtime < 90:
            features["runtime:short"] = 0.5
        elif runtime < 120:
            features["runtime:medium"] = 0.5
        elif runtime < 150:
            features["runtime:long"] = 0.5
        else:
            features["runtime:epic"] = 0.5

    if original_language:
        lang = original_language.strip().lower()
        if lang:
            features[f"lang:{lang}"] = 0.85

    for c in countries or []:
        code = str(c).strip().upper()
        if code:
            features[f"country:{code}"] = features.get(f"country:{code}", 0.0) + 0.8

    for tone, weight in tones_from_keywords(kw_list).items():
        features[f"tone:{tone}"] = max(features.get(f"tone:{tone}", 0.0), weight)

    return features


def build_title_embedding(
    *,
    name: str,
    overview: str | None,
    genres: Iterable[str],
    keywords: Iterable[str],
    people: Iterable[PersonSignal | tuple[str, str] | tuple[str, str, int | None] | str],
    media_type: str,
    release_year: int | None,
    runtime: int | None,
    popularity: float,
    vote_average: float,
    original_language: str | None = None,
    countries: Iterable[str] | None = None,
) -> list[float]:
    """Hashing-trick embedding aligned with the sparse feature schema."""
    vec = [0.0] * EMBEDDING_DIM

    _add(vec, f"media:{media_type}", 1.2)
    for i, g in enumerate(genres):
        _add(vec, f"genre:{str(g).lower()}", 2.0 if i == 0 else 1.4)

    kw_list = filter_keywords(keywords, limit=20)
    for k in kw_list:
        _add(vec, f"kw:{k}", 1.35)

    for person in normalize_person_signals(people):
        if not person.name.strip():
            continue
        _add(vec, person.feature_key(), person.embed_weight())

    if release_year:
        decade = (release_year // 10) * 10
        _add(vec, f"decade:{decade}", 1.1)
        _add(vec, f"year:{release_year}", 0.25)

    if runtime is not None:
        if runtime < 90:
            bucket = "short"
        elif runtime < 120:
            bucket = "medium"
        elif runtime < 150:
            bucket = "long"
        else:
            bucket = "epic"
        _add(vec, f"runtime:{bucket}", 0.7)

    if original_language:
        _add(vec, f"lang:{original_language.strip().lower()}", 1.0)

    for c in countries or []:
        code = str(c).strip().upper()
        if code:
            _add(vec, f"country:{code}", 0.95)

    for tone, weight in tones_from_keywords(kw_list).items():
        _add(vec, f"tone:{tone}", 1.1 * weight)

    # Soft popularity / quality signals (secondary to taste)
    pop_bucket = min(int(popularity // 20), 10)
    _add(vec, f"pop:{pop_bucket}", 0.25)
    rating_bucket = int(round(vote_average))
    _add(vec, f"rating:{rating_bucket}", 0.35)

    for token in tokenize(name)[:10]:
        _add(vec, f"title:{token}", 0.45)
    for token in tokenize(overview)[:28]:
        _add(vec, f"plot:{token}", 0.28)

    return _l2_normalize(vec)


def build_title_signals(
    *,
    name: str,
    overview: str | None,
    genres: Iterable[str],
    keywords: Iterable[str],
    people: Iterable[PersonSignal | tuple[str, str] | tuple[str, str, int | None] | str],
    media_type: str,
    release_year: int | None,
    runtime: int | None,
    popularity: float,
    vote_average: float,
    original_language: str | None = None,
    countries: Sequence[str] | None = None,
) -> tuple[list[float], dict[str, float], dict[str, Any]]:
    """Embedding + sparse features + metadata package for ingest / re-embed."""
    country_list = [str(c).strip().upper() for c in (countries or []) if str(c).strip()]
    kw_list = filter_keywords(keywords, limit=20)
    person_list = normalize_person_signals(people)
    features = features_from_title(
        genres=genres,
        keywords=kw_list,
        people=person_list,
        release_year=release_year,
        runtime=runtime,
        media_type=media_type,
        original_language=original_language,
        countries=country_list,
    )
    embedding = build_title_embedding(
        name=name,
        overview=overview,
        genres=genres,
        keywords=kw_list,
        people=person_list,
        media_type=media_type,
        release_year=release_year,
        runtime=runtime,
        popularity=popularity,
        vote_average=vote_average,
        original_language=original_language,
        countries=country_list,
    )
    tone_tags = sorted(tones_from_keywords(kw_list).keys())
    meta = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "origin_countries": country_list,
        "tone_tags": tone_tags,
        "feature_snapshot": features,
    }
    return embedding, features, meta


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
    limit: int = 8,
    positive_only: bool = True,
) -> list[tuple[str, float]]:
    """Rank overlapping features by user strength × title weight × family weight."""
    scored: list[tuple[str, float]] = []
    for key, t_w in title_features.items():
        if str(key).startswith("__"):
            continue
        raw_u = user_features.get(key, 0.0)
        try:
            u_w = float(raw_u or 0.0)
            t_val = float(t_w)
        except (TypeError, ValueError):
            continue
        if positive_only and u_w <= 0:
            continue
        if not positive_only and u_w == 0:
            continue
        if t_val <= 0:
            continue
        strength = abs(u_w) * t_val * family_score_weight(key)
        scored.append((key, strength if u_w > 0 else -strength))
    scored.sort(key=lambda x: abs(x[1]), reverse=True)
    return scored[:limit]


def sparse_channel_scores(
    user_features: dict[str, Any],
    title_features: dict[str, float],
) -> tuple[float, float]:
    """Return (positive_overlap, negative_penalty) in roughly [0, ~1.5] range."""
    pos = 0.0
    neg = 0.0
    for key, t_w in title_features.items():
        if str(key).startswith("__"):
            continue
        raw_u = user_features.get(key, 0.0)
        try:
            u_w = float(raw_u or 0.0)
            t_val = float(t_w)
        except (TypeError, ValueError):
            continue
        if u_w == 0.0 or t_val <= 0:
            continue
        fam = family_score_weight(key)
        # Cap single-key influence so one favorite director cannot explode score.
        capped_user = min(abs(u_w), 3.0)
        contrib = fam * capped_user * t_val * 0.085
        if u_w > 0:
            pos += contrib
        else:
            neg += contrib
    # Soft saturate
    pos = math.tanh(pos)
    neg = math.tanh(neg)
    return pos, neg
