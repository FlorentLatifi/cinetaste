"""Human-readable recommendation reasons.

Goals:
- Reference specific titles the user rated highly when possible
- Combine signals (director + themes, tone + genre) instead of listing genres alone
- Sound thoughtful, not like a debug dump of feature keys
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.domain.taste_signals import EXPLAIN_ANCHOR_MIN_WEIGHT
from app.recommendation.embeddings import top_feature_overlap

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


# Lowercase connectors that stay lowercase inside a name ("Ludwig van Beethoven").
_NAME_PARTICLES = frozenset(
    {"de", "del", "della", "di", "da", "van", "von", "der", "den", "la", "le", "bin", "al", "el", "du", "dos"}
)
# Surnames whose capitals cannot be derived from a rule. Only a fallback: real
# casing comes from ``display_names`` stored at ingest.
_NAME_EXCEPTIONS = {"dicaprio": "DiCaprio", "dicamillo": "DiCamillo", "desouza": "DeSouza"}
_ROMAN_SUFFIXES = frozenset({"ii", "iii", "iv", "vi"})


def _cap_name_token(token: str, *, first: bool) -> str:
    low = token.lower()
    if low in _NAME_EXCEPTIONS:
        return _NAME_EXCEPTIONS[low]
    if not first and low in _NAME_PARTICLES:
        return low
    if low in _ROMAN_SUFFIXES:
        return low.upper()
    if "-" in low:
        return "-".join(_cap_name_token(p, first=True) for p in low.split("-"))
    if "'" in low:
        return "'".join(_cap_name_token(p, first=True) for p in low.split("'"))
    if low.startswith("mc") and len(low) > 3:
        return "Mc" + low[2:].capitalize()
    return low.capitalize()


def _pretty_person(raw: str, display: str | None = None) -> str:
    """Display a person: stored display name if known, else best-effort casing."""
    if display and display.strip():
        return display.strip()
    parts = raw.replace("_", " ").split()
    return " ".join(_cap_name_token(p, first=i == 0) for i, p in enumerate(parts))


def _display_book(
    title_extra: dict[str, Any] | None, anchors: list[dict[str, Any]]
) -> dict[str, str]:
    """Lowercased person name -> original casing, from the title and the anchors."""
    book: dict[str, str] = {}
    sources = [(title_extra or {}).get("display_names")] + [a.get("display_names") for a in anchors]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, shown in source.items():
            parts = str(key).split(":", 2)
            if len(parts) == 3 and isinstance(shown, str) and shown.strip():
                book.setdefault(parts[2].lower(), shown.strip())
    return book


def _pretty_label(raw: str) -> str:
    return raw.replace("_", " ").strip()


# Keywords that are only a decade/year/number carry no theme ("1950s", "21st century").
_NUMERIC_KEYWORD_RE = re.compile(
    r"^(?:the\s+|year\s+)?(?:'?\d{2,4}'?s?|\d+(?:st|nd|rd|th)\s+century|[\d\W]+)$", re.IGNORECASE
)


def _is_thread_keyword(keyword: str) -> bool:
    k = keyword.strip()
    return bool(k) and not _NUMERIC_KEYWORD_RE.match(k)


# TMDb genre (lowercased) -> (plural noun phrase, adjective/label).
_GENRE_LABELS: dict[str, tuple[str, str]] = {
    "action": ("action films", "action"),
    "adventure": ("adventures", "adventure"),
    "animation": ("animated films", "animated"),
    "comedy": ("comedies", "comedy"),
    "crime": ("crime films", "crime"),
    "documentary": ("documentaries", "documentary"),
    "drama": ("dramas", "drama"),
    "family": ("family films", "family"),
    "fantasy": ("fantasy films", "fantasy"),
    "history": ("historical films", "historical"),
    "horror": ("horror films", "horror"),
    "music": ("music films", "music"),
    "mystery": ("mysteries", "mystery"),
    "romance": ("romances", "romance"),
    "science fiction": ("sci-fi", "sci-fi"),
    "tv movie": ("TV movies", "TV-movie"),
    "thriller": ("thrillers", "thriller"),
    "war": ("war films", "war"),
    "western": ("westerns", "western"),
    # TMDb TV genres
    "action & adventure": ("action-adventure", "action-adventure"),
    "sci-fi & fantasy": ("sci-fi and fantasy", "sci-fi and fantasy"),
    "war & politics": ("war and political dramas", "war and political"),
    "kids": ("kids' shows", "kids'"),
    "news": ("news programs", "news"),
    "reality": ("reality TV", "reality"),
    "soap": ("soap operas", "soap-opera"),
    "talk": ("talk shows", "talk-show"),
}

_TONE_LABELS = {
    "coming_of_age": "coming-of-age",
    "melancholy": "melancholic",
    "horror": "horror-tinged",
    "war": "war-themed",
    "anime": "anime-style",
    "action_heavy": "action-packed",
}
# Tones that merely restate a genre ("action-packed action films").
_TONE_ECHOES_GENRE = {
    "action_heavy": "action",
    "horror": "horror",
    "war": "war",
    "romantic": "romance",
    "comedic": "comedy",
    "anime": "animation",
}

_COUNTRY_ADJECTIVES = {
    "US": "American", "GB": "British", "UK": "British", "CA": "Canadian", "AU": "Australian",
    "NZ": "New Zealand", "IE": "Irish", "FR": "French", "DE": "German", "IT": "Italian",
    "ES": "Spanish", "PT": "Portuguese", "NL": "Dutch", "BE": "Belgian", "SE": "Swedish",
    "NO": "Norwegian", "DK": "Danish", "FI": "Finnish", "IS": "Icelandic", "PL": "Polish",
    "CZ": "Czech", "AT": "Austrian", "CH": "Swiss", "GR": "Greek", "TR": "Turkish",
    "RU": "Russian", "UA": "Ukrainian", "HU": "Hungarian", "RO": "Romanian",
    "JP": "Japanese", "KR": "South Korean", "CN": "Chinese", "HK": "Hong Kong", "TW": "Taiwanese",
    "TH": "Thai", "IN": "Indian", "ID": "Indonesian", "PH": "Filipino", "VN": "Vietnamese",
    "IR": "Iranian", "IL": "Israeli", "EG": "Egyptian", "ZA": "South African", "NG": "Nigerian",
    "MX": "Mexican", "BR": "Brazilian", "AR": "Argentine", "CL": "Chilean", "CO": "Colombian",
}

_LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "de": "German", "it": "Italian", "es": "Spanish",
    "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish", "no": "Norwegian", "nb": "Norwegian",
    "da": "Danish", "fi": "Finnish", "pl": "Polish", "cs": "Czech", "hu": "Hungarian",
    "ro": "Romanian", "el": "Greek", "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "cn": "Cantonese", "th": "Thai",
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam", "bn": "Bengali",
    "id": "Indonesian", "vi": "Vietnamese", "fa": "Persian", "he": "Hebrew", "ar": "Arabic",
}


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
        name = str(key)[len(prefix) :]
        if prefix == "kw:" and not _is_thread_keyword(name):
            continue
        scored.append((name, w))
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
    display_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compact metadata stored when the user positively rates a title.

    ``display_names`` maps person feature keys to original casing; only the
    people kept on the anchor are retained.
    """
    snap = {str(k): v for k, v in (feature_snapshot or {}).items()}
    directors = _keys_with_prefix(snap, "person:director:", limit=3)
    cast = _keys_with_prefix(snap, "person:cast:", limit=4)
    writers = _keys_with_prefix(snap, "person:writer:", limit=2)
    kept = {
        **{f"person:director:{n}": n for n in directors},
        **{f"person:cast:{n}": n for n in cast},
        **{f"person:writer:{n}": n for n in writers},
    }
    names = {k: str(display_names[k]) for k in kept if display_names and display_names.get(k)}
    return {
        "title_id": str(title_id),
        "name": name,
        "event_type": event_type,
        "weight": round(float(weight), 4),
        "year": year,
        "display_names": names,
        "directors": directors,
        "cast": cast,
        "writers": writers,
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
    labels = [_TONE_LABELS.get(t, _pretty_label(t)) for t in tones[:3]]
    return _join_names(labels, limit=3)


def _genre_label(genre: str) -> tuple[str, str]:
    key = genre.replace("_", " ").strip().lower()
    known = _GENRE_LABELS.get(key)
    if known:
        return known
    return (f"{key} films", key)


def _genre_phrase(genres: list[str]) -> str:
    """Plural noun phrase: 'mysteries', 'sci-fi', 'crime films and dramas'."""
    nouns = list(dict.fromkeys(_genre_label(g)[0] for g in genres[:3] if g.strip()))
    return _join_names(nouns, limit=3) if nouns else "films"


def _genre_adjective(genres: list[str]) -> str:
    """Modifier form for 'the {x} side of your taste': 'mystery', 'sci-fi and crime'."""
    adjs = list(dict.fromkeys(_genre_label(g)[1] for g in genres[:3] if g.strip()))
    return _join_names(adjs, limit=3) if adjs else "film"


def _with_article(phrase: str) -> str:
    return f"{'an' if phrase[:1].lower() in 'aeiou' else 'a'} {phrase}"


def _rating_verb(anchors: list[dict[str, Any]], names: list[str]) -> str:
    """'rated' only when every cited title was explicitly rated, else 'liked'."""
    cited = [a for a in anchors if str(a.get("name")) in set(names)]
    if cited and all(str(a.get("event_type") or "").startswith("rate_") for a in cited):
        return "rated"
    return "liked"


def _cite(anchors: list[dict[str, Any]], names: list[str]) -> str:
    """'rated X highly' only when every cited title was explicitly rated."""
    joined = _join_names(names)
    if _rating_verb(anchors, names) == "rated":
        return f"you rated {joined} highly"
    return f"you liked {joined}"


def _variant(seed: str, count: int) -> int:
    """Stable (cross-process) pick so one card always reads the same."""
    return zlib.crc32(seed.encode("utf-8")) % count


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
    book = _display_book(title_extra, anchors)

    def _who(raw: str) -> str:
        return _pretty_person(raw, book.get(raw.lower()))

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
        # A shared genre alone is not a reason to name a specific favorite.
        if not any(overlap[k] for k in ("directors", "cast", "writers", "tones", "keywords")):
            continue
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
        top_anchors = [anchor for _s, anchor, _o in top]
        by_director: dict[str, list[str]] = {}
        for _s, anchor, overlap in top:
            for d in overlap["directors"]:
                by_director.setdefault(d, []).append(str(anchor.get("name") or "a favorite"))

        if by_director:
            director, liked = max(by_director.items(), key=lambda kv: len(set(kv[1])))
            liked_unique = list(dict.fromkeys(liked))[:2]
            shared_kw: list[str] = []
            shared_tones: list[str] = []
            for _s, _anchor, overlap in top:
                if director in (overlap.get("directors") or []):
                    shared_kw.extend(overlap.get("keywords") or [])
                    shared_tones.extend(overlap.get("tones") or [])
            shared_kw = list(dict.fromkeys(shared_kw))[:2]
            shared_tones = list(dict.fromkeys(shared_tones))[:2]

            if shared_tones:
                echo = f"{_tone_phrase(shared_tones)} storytelling"
            elif shared_kw:
                echo = f"a shared {_pretty_label(shared_kw[0])} theme"
            else:
                echo = ""
            cite = _cite(top_anchors, liked_unique)
            who = _who(director)
            # Vary the construction (stable per card) so a slate does not read as a template.
            style = _variant(f"{title_name}|{director}", 3)
            if style == 0:
                detail = f"same director + {echo}" if echo else "same director"
                lead = f"Because {cite} ({detail} — {who})"
            elif style == 1:
                tail = f", with {echo}" if echo else ""
                lead = f"{cite[0].upper()}{cite[1:]}, and this is from the same director, {who}{tail}"
            else:
                tail = f" ({echo})" if echo else ""
                lead = f"{who} again: {cite}{tail}"
            _add(
                Reason(
                    code="because_you_liked",
                    message=lead,
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
                link_bits.append(_with_article(_tone_phrase(best_overlap["tones"][:2])) + " tone")
            if best_overlap["keywords"]:
                link_bits.append(
                    "themes like " + ", ".join(_pretty_label(k) for k in best_overlap["keywords"][:2])
                )
            if best_overlap["cast"]:
                link_bits.append(f"cast in common ({_who(best_overlap['cast'][0])})")
            if best_overlap["writers"]:
                link_bits.append(f"a writer in common ({_who(best_overlap['writers'][0])})")
            if best_overlap["genres"] and len(link_bits) < 2:
                link_bits.append(f"a shared {_genre_adjective(best_overlap['genres'][:1])} streak")
            bridge = ", plus ".join(link_bits[:2])
            cite = _cite(top_anchors, liked)
            if _variant(f"{title_name}|{liked[0]}", 2) == 0:
                lead = f"Because {cite} — {bridge}"
            else:
                lead = f"{cite[0].upper()}{cite[1:]}, and this one shares {bridge}"
            _add(
                Reason(
                    code="because_you_liked",
                    message=lead,
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
    kw_hits = [
        k.split(":", 1)[1]
        for k, s in overlap_feats
        if s > 0 and k.startswith("kw:") and _is_thread_keyword(k.split(":", 1)[1])
    ]

    if tone_hits and genre_hits and len(reasons) < max_reasons and any(
        _TONE_ECHOES_GENRE.get(t) not in set(genre_hits) for t in tone_hits
    ):
        # Craft "Strong match on tense psychological thrillers..."
        # Drop tones that only restate the genre ("action-packed action films").
        blend_tones = [t for t in tone_hits if _TONE_ECHOES_GENRE.get(t) not in set(genre_hits)]
        tone_bit = _tone_phrase(blend_tones[:2])
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
        # Only mention a theme that is an actual keyword on both sides.
        if complex_kw:
            extra = f" with {_pretty_label(complex_kw[0])}"
        msg = f"Strong match on {tone_bit} {genre_bit}{extra}".replace("  ", " ").strip()
        _add(
            Reason(
                code="taste_blend",
                message=msg,
                evidence={"tones": blend_tones[:3], "genres": genre_hits[:3], "keywords": kw_hits[:3]},
            )
        )

    # --- 3) Shared tone (satirical, dark, whimsical, ...) ---
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
                    message=f"Same {tone_desc} tone as {_join_names(liked_style)}",
                    evidence={"tones": style_tones, "liked_titles": liked_style},
                )
            )
        else:
            _add(
                Reason(
                    code="similar_style",
                    message=f"Matches the {tone_desc} tone you tend to enjoy",
                    evidence={"tones": style_tones},
                )
            )

    # --- 4) Director / cast without repeating because_you_liked director beat ---
    if not any(r.code == "because_you_liked" and r.evidence.get("directors") for r in reasons):
        dir_overlap = [
            k.split(":", 2)[-1]
            for k, s in overlap_feats
            if s > 0 and k.startswith("person:director:")
        ]
        if dir_overlap and len(reasons) < max_reasons:
            name = _who(dir_overlap[0])
            liked_anchors = [
                a
                for a in anchors
                if dir_overlap[0] in (a.get("directors") or [])
                and float(a.get("weight") or 0) >= _ANCHOR_MIN_WEIGHT
            ][:2]
            liked = [str(a.get("name")) for a in liked_anchors]
            if liked:
                tail = "you rated highly" if _rating_verb(liked_anchors, liked) == "rated" else "you liked"
                _add(
                    Reason(
                        code="same_director",
                        message=f"From {name}, whose {_join_names(liked)} {tail}",
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
        name = _who(cast_overlap[0])
        _add(
            Reason(
                code="similar_cast",
                message=f"Features {name}, an actor you've responded well to before",
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
            shared_themes = [
                k
                for k in kw_hits[:4]
                if any(k in (a.get("keywords") or []) for a in anchors if str(a.get("name")) in theme_titles)
            ][:3]
            _add(
                Reason(
                    code="similar_themes",
                    message=(
                        f"Shares themes with {_join_names(theme_titles)}: "
                        + ", ".join(_pretty_label(k) for k in shared_themes)
                    ),
                    evidence={"keywords": shared_themes, "liked_titles": theme_titles},
                )
            )
        else:
            _add(
                Reason(
                    code="similar_themes",
                    message=f"Picks up themes you gravitate toward: {themes}",
                    evidence={"keywords": kw_hits[:3]},
                )
            )

    # --- 6) Origin / era / language (only if still need slots) ---
    # Each entry renders a hit into a phrase, or None when the code has no
    # human name (then we say nothing rather than print a raw code).
    def _origin(code: str) -> str | None:
        adj = _COUNTRY_ADJECTIVES.get(code.upper())
        return f"Part of the {adj} cinema you've been rewarding" if adj else None

    def _language(code: str) -> str | None:
        name = _LANGUAGE_NAMES.get(code.lower())
        # "English-language" is true of nearly the whole catalog: not a reason.
        if not name or code.lower() == "en":
            return None
        return f"A {name}-language pick, in a lane you already enjoy"

    def _era(decade: str) -> str | None:
        return f"From the {decade}s — an era that scores well for you" if decade.isdigit() else None

    for prefix, code, render in (
        ("country:", "similar_origin", _origin),
        ("lang:", "similar_language", _language),
        ("decade:", "similar_era", _era),
    ):
        if len(reasons) >= max_reasons:
            break
        hits = [k.split(":", 1)[1] for k, s in overlap_feats if s > 0 and k.startswith(prefix)]
        message = next((m for m in (render(h) for h in hits) if m), None)
        if not message:
            continue
        _add(Reason(code=code, message=message, evidence={code: hits[:2]}))

    # --- 7) Genre only as last structural resort, still humanized ---
    if len(reasons) < max_reasons and genre_hits:
        liked_genre_titles = [
            str(a.get("name"))
            for a in anchors
            if set(a.get("genres") or []) & set(genre_hits[:2])
            and float(a.get("weight") or 0) >= _ANCHOR_MIN_WEIGHT
        ][:2]
        gphrase = _genre_adjective(genre_hits[:2])
        if liked_genre_titles:
            _add(
                Reason(
                    code="shared_genre",
                    message=f"In the same {gphrase} vein as {_join_names(liked_genre_titles)}",
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
                    message=f"A thoughtful {_genre_adjective(title_genres[:1])} pick while we learn more about you",
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

    return reasons[:max_reasons]


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def anchor_min_weight() -> float:
    return _ANCHOR_MIN_WEIGHT
