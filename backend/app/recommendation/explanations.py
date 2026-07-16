"""Human-readable recommendation reasons.

Goals:
- Reference specific titles the user rated highly when possible
- Combine signals (director + themes, tone + genre) instead of listing genres alone
- Sound thoughtful, not like a debug dump of feature keys
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.recommendation.embeddings import top_feature_overlap

from app.domain.taste_signals import EXPLAIN_ANCHOR_MIN_WEIGHT

# Stored inside TasteProfile.features (stripped before scoring).
EXPLAIN_MEMORY_KEY = "__explain_memory__"

# Positive interactions strong enough to cite as "you liked X" (policy).
_ANCHOR_MIN_WEIGHT = EXPLAIN_ANCHOR_MIN_WEIGHT
_MAX_ANCHORS = 16


@dataclass
class Reason:
    code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


def strip_explain_memory(features: dict[str, Any] | None) -> tuple[dict[str, float], dict[str, Any]]:
    """Split scoring features from explain memory stored in the same JSON map."""
    if not features:
        return {}, {}
    memory: dict[str, Any] = {}
    clean: dict[str, float] = {}
    for key, value in features.items():
        if str(key).startswith("__"):
            if key == EXPLAIN_MEMORY_KEY and isinstance(value, dict):
                memory = value
            continue
        try:
            clean[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return clean, memory


def _pretty_person(raw: str) -> str:
    return " ".join(part.capitalize() for part in raw.replace("_", " ").split())


def _pretty_label(raw: str) -> str:
    return raw.replace("_", " ").strip()


def _join_names(names: list[str], *, limit: int = 2) -> str:
    names = [n for n in names if n][:limit]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _keys_with_prefix(snapshot: dict[str, Any], prefix: str, *, limit: int = 8) -> list[str]:
    scored: list[tuple[str, float]] = []
    for key, value in snapshot.items():
        if not str(key).startswith(prefix):
            continue
        try:
            w = float(value)
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        scored.append((str(key)[len(prefix) :], w))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in scored[:limit]]


def build_anchor_from_title(
    *,
    title_id: UUID,
    name: str,
    event_type: str,
    weight: float,
    feature_snapshot: dict[str, Any] | None,
    year: int | None = None,
) -> dict[str, Any]:
    """Compact metadata stored when the user positively rates a title."""
    snap = {str(k): v for k, v in (feature_snapshot or {}).items()}
    return {
        "title_id": str(title_id),
        "name": name,
        "event_type": event_type,
        "weight": round(float(weight), 4),
        "year": year,
        "directors": _keys_with_prefix(snap, "person:director:", limit=3),
        "cast": _keys_with_prefix(snap, "person:cast:", limit=4),
        "writers": _keys_with_prefix(snap, "person:writer:", limit=2),
        "tones": _keys_with_prefix(snap, "tone:", limit=4),
        "keywords": _keys_with_prefix(snap, "kw:", limit=8),
        "genres": _keys_with_prefix(snap, "genre:", limit=4),
        "decades": _keys_with_prefix(snap, "decade:", limit=1),
        "countries": _keys_with_prefix(snap, "country:", limit=2),
        "languages": _keys_with_prefix(snap, "lang:", limit=1),
    }


def merge_explain_memory(
    features: dict[str, float],
    anchors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach explain memory without polluting sparse feature keys used for scoring."""
    # Prefer highest weight, keep recency order among equals (caller orders events).
    ranked = sorted(anchors, key=lambda a: float(a.get("weight") or 0), reverse=True)
    # Dedupe by title_id keeping strongest
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for a in ranked:
        tid = str(a.get("title_id") or "")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        unique.append(a)
        if len(unique) >= _MAX_ANCHORS:
            break
    out: dict[str, Any] = dict(features)
    out[EXPLAIN_MEMORY_KEY] = {"version": 1, "anchors": unique}
    return out


def _feature_snapshot(title_extra: dict[str, Any] | None) -> dict[str, float]:
    snap = (title_extra or {}).get("feature_snapshot") or {}
    out: dict[str, float] = {}
    for k, v in snap.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _anchor_overlap(
    anchor: dict[str, Any],
    *,
    directors: set[str],
    cast: set[str],
    writers: set[str],
    tones: set[str],
    keywords: set[str],
    genres: set[str],
) -> dict[str, list[str]]:
    return {
        "directors": sorted(directors & set(anchor.get("directors") or [])),
        "cast": sorted(cast & set(anchor.get("cast") or [])),
        "writers": sorted(writers & set(anchor.get("writers") or [])),
        "tones": sorted(tones & set(anchor.get("tones") or [])),
        "keywords": sorted(keywords & set(anchor.get("keywords") or [])),
        "genres": sorted(genres & set(anchor.get("genres") or [])),
    }


def _overlap_score(overlap: dict[str, list[str]]) -> float:
    return (
        3.0 * len(overlap["directors"])
        + 1.6 * len(overlap["cast"])
        + 1.4 * len(overlap["writers"])
        + 1.8 * len(overlap["tones"])
        + 1.2 * len(overlap["keywords"])
        + 0.8 * len(overlap["genres"])
    )


def _tone_phrase(tones: list[str]) -> str:
    labels = [_pretty_label(t) for t in tones[:3]]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{labels[0]}, {labels[1]}, and {labels[2]}"


def _genre_phrase(genres: list[str]) -> str:
    labels = [g.replace("_", " ").title() for g in genres[:3]]
    if not labels:
        return "films"
    if len(labels) == 1:
        return f"{labels[0].lower()}s" if not labels[0].endswith("s") else labels[0].lower()
    return " / ".join(g.lower() for g in labels)


def build_reasons(
    *,
    user_features: dict[str, Any],
    explain_memory: dict[str, Any] | None,
    title_name: str | None,
    title_extra: dict[str, Any] | None,
    title_genres: list[str],
    similarity: float,
    max_reasons: int = 3,
) -> list[Reason]:
    """Produce up to ``max_reasons`` specific, human-like explanations."""
    scoring_features, _ = strip_explain_memory(user_features)
    # If caller already stripped, scoring_features == user_features
    if not scoring_features and user_features:
        scoring_features = {
            str(k): float(v)
            for k, v in user_features.items()
            if not str(k).startswith("__")
            and _is_number(v)
        }

    title_features = _feature_snapshot(title_extra)
    anchors: list[dict[str, Any]] = list((explain_memory or {}).get("anchors") or [])

    cand_directors = set(_keys_with_prefix(title_features, "person:director:"))
    cand_cast = set(_keys_with_prefix(title_features, "person:cast:"))
    cand_writers = set(_keys_with_prefix(title_features, "person:writer:"))
    cand_tones = set(_keys_with_prefix(title_features, "tone:"))
    cand_keywords = set(_keys_with_prefix(title_features, "kw:"))
    cand_genres = set(_keys_with_prefix(title_features, "genre:"))
    if not cand_genres and title_genres:
        cand_genres = {g.lower() for g in title_genres}

    # Score anchors by shared creative DNA with the candidate.
    ranked_anchors: list[tuple[float, dict[str, Any], dict[str, list[str]]]] = []
    for anchor in anchors:
        if float(anchor.get("weight") or 0) < _ANCHOR_MIN_WEIGHT * 0.5:
            continue
        overlap = _anchor_overlap(
            anchor,
            directors=cand_directors,
            cast=cand_cast,
            writers=cand_writers,
            tones=cand_tones,
            keywords=cand_keywords,
            genres=cand_genres,
        )
        score = _overlap_score(overlap) * (0.6 + 0.4 * min(float(anchor.get("weight") or 0) / 1.55, 1.0))
        if score > 0:
            ranked_anchors.append((score, anchor, overlap))
    ranked_anchors.sort(key=lambda x: x[0], reverse=True)

    reasons: list[Reason] = []
    used_codes: set[str] = set()

    def _add(reason: Reason) -> None:
        if reason.code in used_codes and reason.code not in {"because_you_liked", "thematic_bridge"}:
            return
        # Allow multiple because_you_liked only if messages differ substantially
        if any(r.message == reason.message for r in reasons):
            return
        reasons.append(reason)
        used_codes.add(reason.code)

    # --- 1) "Because you rated X and Y highly (...)" ---
    if ranked_anchors:
        top = ranked_anchors[:3]
        # Prefer multi-title cite when same director/theme bridges several favorites
        by_director: dict[str, list[str]] = {}
        for _s, anchor, overlap in top:
            for d in overlap["directors"]:
                by_director.setdefault(d, []).append(str(anchor.get("name") or "a favorite"))

        if by_director:
            director, liked = max(by_director.items(), key=lambda kv: len(set(kv[1])))
            liked_unique = list(dict.fromkeys(liked))[:2]
            shared_kw: list[str] = []
            shared_tones: list[str] = []
            for _s, anchor, overlap in top:
                if director in (overlap.get("directors") or []):
                    shared_kw.extend(overlap.get("keywords") or [])
                    shared_tones.extend(overlap.get("tones") or [])
            shared_kw = list(dict.fromkeys(shared_kw))[:2]
            shared_tones = list(dict.fromkeys(shared_tones))[:2]

            bits: list[str] = ["same director"]
            if shared_tones:
                bits.append(_tone_phrase(shared_tones) + " storytelling")
            elif shared_kw:
                bits.append(_pretty_label(shared_kw[0]) + " threads")
            detail = " + ".join(bits)
            names = _join_names(liked_unique)
            _add(
                Reason(
                    code="because_you_liked",
                    message=(
                        f"Because you rated {names} highly "
                        f"({detail} — {_pretty_person(director)})"
                    ),
                    evidence={
                        "liked_titles": liked_unique,
                        "directors": [director],
                        "keywords": shared_kw,
                        "tones": shared_tones,
                    },
                )
            )
        else:
            # Multi-signal bridge without shared director
            best_score, best_anchor, best_overlap = top[0]
            second_names = [
                str(a.get("name"))
                for _s, a, o in top[1:3]
                if _overlap_score(o) >= best_score * 0.45
            ]
            liked = [str(best_anchor.get("name") or "titles you love")] + second_names
            liked = list(dict.fromkeys(liked))[:2]
            link_bits: list[str] = []
            if best_overlap["tones"]:
                link_bits.append(_tone_phrase(best_overlap["tones"]))
            if best_overlap["keywords"]:
                link_bits.append(
                    "themes like " + ", ".join(_pretty_label(k) for k in best_overlap["keywords"][:2])
                )
            if best_overlap["cast"]:
                link_bits.append(f"cast energy from {_pretty_person(best_overlap['cast'][0])}")
            if best_overlap["genres"] and len(link_bits) < 2:
                link_bits.append(_genre_phrase(best_overlap["genres"]))
            if not link_bits:
                link_bits.append("a similar vibe")
            bridge = " and ".join(link_bits[:2])
            _add(
                Reason(
                    code="because_you_liked",
                    message=f"Because you rated {_join_names(liked)} highly — {bridge}",
                    evidence={
                        "liked_titles": liked,
                        "overlap": {k: v for k, v in best_overlap.items() if v},
                    },
                )
            )

    # --- 2) Strong multi-signal taste match (tone + genre / psychological blend) ---
    overlap_feats = top_feature_overlap(scoring_features, title_features, limit=20)
    tone_hits = [k.split(":", 1)[1] for k, s in overlap_feats if s > 0 and k.startswith("tone:")]
    genre_hits = [k.split(":", 1)[1] for k, s in overlap_feats if s > 0 and k.startswith("genre:")]
    kw_hits = [k.split(":", 1)[1] for k, s in overlap_feats if s > 0 and k.startswith("kw:")]

    if tone_hits and genre_hits and len(reasons) < max_reasons:
        # Craft "Strong match on tense psychological thrillers..."
        tone_bit = _tone_phrase(tone_hits[:2])
        genre_bit = _genre_phrase(genre_hits[:2])
        extra = ""
        complex_kw = [
            k
            for k in kw_hits
            if any(
                x in k
                for x in (
                    "moral",
                    "psychological",
                    "identity",
                    "revenge",
                    "conspiracy",
                    "philosophy",
                    "time",
                    "memory",
                )
            )
        ]
        if complex_kw:
            extra = f" with {_pretty_label(complex_kw[0])}"
        elif any(t in {"tense", "dark", "cerebral", "bleak", "meditative"} for t in tone_hits):
            gjoin = " ".join(genre_hits)
            if "thriller" in gjoin or "mystery" in gjoin:
                if "tense" in tone_hits or "dark" in tone_hits or "cerebral" in tone_hits:
                    extra = " with moral complexity"
        msg = f"Strong match on {tone_bit} {genre_bit}{extra}".replace("  ", " ").strip()
        _add(
            Reason(
                code="taste_blend",
                message=msg,
                evidence={"tones": tone_hits[:3], "genres": genre_hits[:3], "keywords": kw_hits[:3]},
            )
        )

    # --- 3) Dark humor / pacing style from tones + keywords ---
    style_tones = [t for t in tone_hits if t in {"satirical", "dark", "comedic", "whimsical", "stylish", "tense"}]
    if style_tones and len(reasons) < max_reasons:
        liked_style = [
            str(a.get("name"))
            for a in anchors
            if set(a.get("tones") or []) & set(style_tones) and float(a.get("weight") or 0) >= _ANCHOR_MIN_WEIGHT
        ][:2]
        tone_desc = _tone_phrase(style_tones[:2])
        if liked_style:
            _add(
                Reason(
                    code="similar_style",
                    message=(
                        f"Similar {tone_desc} energy and pacing to "
                        f"{_join_names(liked_style)} — movies you've enjoyed"
                    ),
                    evidence={"tones": style_tones, "liked_titles": liked_style},
                )
            )
        else:
            _add(
                Reason(
                    code="similar_style",
                    message=f"Similar {tone_desc} humor and pacing to movies you've enjoyed",
                    evidence={"tones": style_tones},
                )
            )

    # --- 4) Director / cast without repeating because_you_liked director beat ---
    if "because_you_liked" not in used_codes or not any(
        "same director" in (r.message or "") for r in reasons
    ):
        dir_overlap = [
            k.split(":", 2)[-1]
            for k, s in overlap_feats
            if s > 0 and k.startswith("person:director:")
        ]
        if dir_overlap and len(reasons) < max_reasons:
            name = _pretty_person(dir_overlap[0])
            liked = [
                str(a.get("name"))
                for a in anchors
                if dir_overlap[0] in (a.get("directors") or [])
                and float(a.get("weight") or 0) >= _ANCHOR_MIN_WEIGHT
            ][:2]
            if liked:
                _add(
                    Reason(
                        code="same_director",
                        message=(
                            f"From {_pretty_person(dir_overlap[0])}, whose work you rated highly "
                            f"({_join_names(liked)})"
                        ),
                        evidence={"directors": dir_overlap[:2], "liked_titles": liked},
                    )
                )
            else:
                _add(
                    Reason(
                        code="same_director",
                        message=f"You tend to connect with films by {name}",
                        evidence={"directors": dir_overlap[:2]},
                    )
                )

    cast_overlap = [
        k.split(":", 2)[-1]
        for k, s in overlap_feats
        if s > 0 and k.startswith("person:cast:")
    ]
    if cast_overlap and len(reasons) < max_reasons:
        name = _pretty_person(cast_overlap[0])
        _add(
            Reason(
                code="similar_cast",
                message=f"Features {name}, talent you've responded well to before",
                evidence={"cast": cast_overlap[:3]},
            )
        )

    # --- 5) Theme bridge naming favorites ---
    if kw_hits and len(reasons) < max_reasons:
        theme_titles = [
            str(a.get("name"))
            for a in anchors
            if set(a.get("keywords") or []) & set(kw_hits[:4])
            and float(a.get("weight") or 0) >= _ANCHOR_MIN_WEIGHT
        ][:2]
        themes = ", ".join(_pretty_label(k) for k in kw_hits[:3])
        if theme_titles:
            _add(
                Reason(
                    code="similar_themes",
                    message=f"Shares themes with {_join_names(theme_titles)}: {themes}",
                    evidence={"keywords": kw_hits[:3], "liked_titles": theme_titles},
                )
            )
        else:
            _add(
                Reason(
                    code="similar_themes",
                    message=f"Picks up threads you care about: {themes}",
                    evidence={"keywords": kw_hits[:3]},
                )
            )

    # --- 6) Origin / era / language (only if still need slots) ---
    for prefix, code, template in (
        ("country:", "similar_origin", "Leans into {x} cinema you've been rewarding"),
        ("lang:", "similar_language", "In a language lane you already enjoy ({x})"),
        ("decade:", "similar_era", "Sits in the {x}s — an era that scores well for you"),
    ):
        if len(reasons) >= max_reasons:
            break
        hits = [k.split(":", 1)[1] for k, s in overlap_feats if s > 0 and k.startswith(prefix)]
        if not hits:
            continue
        label = hits[0]
        _add(
            Reason(
                code=code,
                message=template.format(x=label),
                evidence={code: hits[:2]},
            )
        )

    # --- 7) Genre only as last structural resort, still humanized ---
    if len(reasons) < max_reasons and genre_hits:
        liked_genre_titles = [
            str(a.get("name"))
            for a in anchors
            if set(a.get("genres") or []) & set(genre_hits[:2])
            and float(a.get("weight") or 0) >= _ANCHOR_MIN_WEIGHT
        ][:2]
        gphrase = _genre_phrase(genre_hits[:2])
        if liked_genre_titles:
            _add(
                Reason(
                    code="shared_genre",
                    message=f"In the same {gphrase} pocket as {_join_names(liked_genre_titles)}",
                    evidence={"genres": genre_hits[:3], "liked_titles": liked_genre_titles},
                )
            )
        else:
            _add(
                Reason(
                    code="shared_genre",
                    message=f"Fits the {gphrase} side of your taste",
                    evidence={"genres": genre_hits[:3]},
                )
            )

    if similarity >= 0.58 and len(reasons) < max_reasons:
        _add(
            Reason(
                code="taste_similarity",
                message="Overall, this sits close to the center of your taste profile",
                evidence={"similarity": round(similarity, 3)},
            )
        )

    if not reasons:
        if title_genres:
            _add(
                Reason(
                    code="genre_fit",
                    message=f"A thoughtful {title_genres[0].lower()} pick while we learn more about you",
                    evidence={"genres": title_genres[:2]},
                )
            )
        else:
            _add(
                Reason(
                    code="discovery",
                    message="A discovery pick to stretch your taste a little",
                    evidence={"candidate": title_name},
                )
            )

    # Prefer fewer, stronger reasons — drop weak trailing genre if we already have rich ones
    if len(reasons) >= 2 and reasons[-1].code in {"shared_genre", "genre_fit", "taste_similarity"}:
        if any(r.code in {"because_you_liked", "taste_blend", "similar_style", "same_director"} for r in reasons[:-1]):
            # keep if under 2 else ok
            pass

    return reasons[:max_reasons]


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def anchor_min_weight() -> float:
    return _ANCHOR_MIN_WEIGHT
