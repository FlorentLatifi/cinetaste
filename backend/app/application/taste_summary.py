"""Human-readable taste profile summary for the Account page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.recommendation.explanations import EXPLAIN_MEMORY_KEY, strip_explain_memory

# Durable sparse overlay applied after event recompute (survives clear/re-rate).
IMPORT_OVERLAY_KEY = "__import_overlay__"

# Soften imported weights so live ratings still dominate.
IMPORT_MERGE_SCALE = 0.65

_ALLOWED_FEATURE_PREFIXES = (
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
    overlay = {}
    if isinstance(raw_features, dict):
        raw_overlay = raw_features.get(IMPORT_OVERLAY_KEY)
        if isinstance(raw_overlay, dict):
            overlay = raw_overlay
    return {
        "likes": likes,
        "dislikes": dislikes,
        "anchor_count": anchor_count,
        "feature_count": len(scoring),
        "has_explain_memory": bool(memory) or EXPLAIN_MEMORY_KEY in (raw_features or {}),
        "anchors": anchors if isinstance(anchors, list) else [],
        "has_import_overlay": bool(overlay),
        "import_overlay_count": len(overlay),
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


def is_allowed_feature_key(key: str) -> bool:
    if not key or key.startswith("__"):
        return False
    return any(key.startswith(p) for p in _ALLOWED_FEATURE_PREFIXES)


def merge_import_overlay(
    existing_overlay: dict[str, Any] | None,
    *,
    likes: list[dict[str, Any]],
    dislikes: list[dict[str, Any]],
    scale: float = IMPORT_MERGE_SCALE,
) -> dict[str, float]:
    """Merge snapshot chips into a durable import overlay map."""
    out: dict[str, float] = {}
    if existing_overlay:
        for k, v in existing_overlay.items():
            if not is_allowed_feature_key(str(k)):
                continue
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue

    def _add(rows: list[dict[str, Any]], *, expect_positive: bool) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = row.get("key")
            if not isinstance(key, str) or not is_allowed_feature_key(key):
                continue
            try:
                weight = float(row.get("weight", 0))
            except (TypeError, ValueError):
                continue
            if expect_positive and weight <= 0:
                continue
            if not expect_positive and weight >= 0:
                continue
            out[key] = out.get(key, 0.0) + weight * scale

    _add(likes, expect_positive=True)
    _add(dislikes, expect_positive=False)
    # Cap so a re-import loop cannot explode the profile
    return {k: max(min(round(v, 4), 4.5), -4.5) for k, v in out.items() if abs(v) > 0.02}


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
