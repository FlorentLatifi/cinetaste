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
        "anchors": anchors if isinstance(anchors, list) else [],
    }


def extract_anchor_names(anchors: list[Any], *, limit: int = 12) -> list[dict[str, Any]]:
    """Public-safe anchor citations (title names only — no internal IDs)."""
    out: list[dict[str, Any]] = []
    for row in anchors[:limit]:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not name or not isinstance(name, str):
            continue
        entry: dict[str, Any] = {"name": name.strip()}
        year = row.get("year")
        if isinstance(year, int) and 1880 < year < 2100:
            entry["year"] = year
        out.append(entry)
    return out


def build_taste_export(
    *,
    profile_version: int,
    updated_at: Any,
    has_vector: bool,
    raw_features: dict[str, Any] | None,
    exported_at: str,
    chip_limit: int = 12,
) -> dict[str, Any]:
    """JSON-serializable snapshot for download / share (no dense embedding)."""
    summary = summarize_profile_features(raw_features, limit=chip_limit)
    likes = [
        {"key": c.key, "family": c.family, "label": c.label, "weight": c.weight}
        for c in summary["likes"]
    ]
    dislikes = [
        {"key": c.key, "family": c.family, "label": c.label, "weight": c.weight}
        for c in summary["dislikes"]
    ]
    anchors = extract_anchor_names(summary.get("anchors") or [])
    return {
        "schema": "cinetaste.taste_snapshot.v1",
        "exported_at": exported_at,
        "profile_version": int(profile_version or 0),
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        "has_vector": bool(has_vector),
        "feature_count": summary["feature_count"],
        "anchor_count": summary["anchor_count"],
        "likes": likes,
        "dislikes": dislikes,
        "anchors": anchors,
    }


def format_taste_export_text(snapshot: dict[str, Any]) -> str:
    """Human-readable share text derived from a taste snapshot dict."""
    lines = [
        "CineTaste — taste snapshot",
        f"Exported: {snapshot.get('exported_at', '—')}",
        f"Profile version: {snapshot.get('profile_version', 0)}",
        "",
    ]
    likes = snapshot.get("likes") or []
    if likes:
        lines.append("You lean toward")
        for row in likes:
            label = row.get("label") if isinstance(row, dict) else str(row)
            lines.append(f"  · {label}")
        lines.append("")
    dislikes = snapshot.get("dislikes") or []
    if dislikes:
        lines.append("You tend to avoid")
        for row in dislikes:
            label = row.get("label") if isinstance(row, dict) else str(row)
            lines.append(f"  · {label}")
        lines.append("")
    anchors = snapshot.get("anchors") or []
    if anchors:
        lines.append("Titles that shape explanations")
        for row in anchors:
            if isinstance(row, dict):
                name = row.get("name", "")
                year = row.get("year")
                lines.append(f"  · {name}" + (f" ({year})" if year else ""))
            else:
                lines.append(f"  · {row}")
        lines.append("")
    if not likes and not dislikes:
        lines.append("(Not enough signal for a readable profile yet.)")
        lines.append("")
    lines.append("— Generated by CineTaste (not a social profile; private to you)")
    return "\n".join(lines)
