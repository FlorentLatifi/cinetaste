"""Human-readable taste profile summary for the Account page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.recommendation.explanations import EXPLAIN_MEMORY_KEY, strip_explain_memory


@dataclass(frozen=True)
class FeatureChip:
    key: str
    family: str
    label: str
    weight: float


_FAMILY_LABELS = {
    "genre:": "Genre",
    "person:director:": "Director",
    "person:writer:": "Writer",
    "person:cast:": "Cast",
    "tone:": "Tone",
    "keyword:": "Theme",
    "lang:": "Language",
    "country:": "Country",
    "decade:": "Decade",
    "runtime:": "Runtime",
    "media:": "Format",
    "other:": "Other",
}


def feature_family_name(key: str) -> str:
    """Best-effort family key for display (mirrors embedding prefixes)."""
    prefixes = (
        "person:director:",
        "person:writer:",
        "person:cast:",
        "genre:",
        "tone:",
        "keyword:",
        "lang:",
        "country:",
        "decade:",
        "runtime:",
        "media:",
    )
    for p in prefixes:
        if key.startswith(p):
            return p
    return "other:"


def humanize_feature_key(key: str) -> tuple[str, str]:
    """Return (family_label, value_label) for a sparse feature key."""
    fam = feature_family_name(key)
    family = _FAMILY_LABELS.get(fam, "Other")
    if fam == "other:":
        return family, key.replace(":", " · ").replace("_", " ").title()

    rest = key[len(fam) :].strip()
    if fam.startswith("person:"):
        value = rest.replace("_", " ").title() if rest else key
        return family, value
    if fam == "decade:":
        return family, rest if rest.endswith("s") else f"{rest}s"
    if fam == "lang:":
        return family, rest.upper() if len(rest) <= 3 else rest.title()
    value = rest.replace("_", " ").replace("-", " ").title()
    return family, value


def rank_features(
    features: dict[str, float],
    *,
    positive: bool,
    limit: int = 8,
) -> list[FeatureChip]:
    scored: list[FeatureChip] = []
    for key, raw in features.items():
        try:
            weight = float(raw)
        except (TypeError, ValueError):
            continue
        if positive and weight <= 0:
            continue
        if not positive and weight >= 0:
            continue
        family_key = feature_family_name(key)
        family_label, value_label = humanize_feature_key(key)
        # Prefer value alone for genres; "Genre · Drama" is redundant in chips
        if family_key == "genre:":
            label = value_label
        else:
            label = f"{family_label}: {value_label}"
        scored.append(
            FeatureChip(
                key=key,
                family=family_key.rstrip(":"),
                label=label,
                weight=round(weight, 3),
            )
        )
    scored.sort(key=lambda c: abs(c.weight), reverse=True)
    return scored[:limit]


def summarize_profile_features(
    raw_features: dict[str, Any] | None,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Build API payload fields from stored TasteProfile.features."""
    scoring, memory = strip_explain_memory(raw_features)
    likes = rank_features(scoring, positive=True, limit=limit)
    dislikes = rank_features(scoring, positive=False, limit=limit)
    anchors = memory.get("anchors") if isinstance(memory, dict) else None
    anchor_count = len(anchors) if isinstance(anchors, list) else 0
    return {
        "likes": likes,
        "dislikes": dislikes,
        "anchor_count": anchor_count,
        "feature_count": len(scoring),
        "has_explain_memory": bool(memory) or EXPLAIN_MEMORY_KEY in (raw_features or {}),
    }
