"""Where a user's positive taste sits by original language and by format.

The ranker caps how much of one slate a single language, or TV, may take —
the same way it caps genres, directors and franchises — but the cap has to
follow the person: someone who only rates English-language films should get
an English-language slate, while someone who loved one Japanese film among
Korean, French and Brazilian ones should not get eleven Japanese titles.

So caps are proportional to the *taste mix*: the share of the user's positive
signal (effective signal weight, after decay) in each original language and in
each format (movie / tv). It is computed from the effective signals when the
profile is built — not from the stored ``lang:`` / ``media:`` features, which
are clamped per key (``profile.FEATURE_CAP``) and so understate a dominant
language for anyone with a long history — and stored on the profile under
``TASTE_MIX_KEY``, beside the explain memory. Profiles built before the key
existed fall back to the positive ``lang:`` / ``media:`` features until their
next rebuild.

Pure functions over plain mappings: no database, no FastAPI.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

TASTE_MIX_KEY = "__taste_mix__"
LANG_PREFIX = "lang:"
MEDIA_PREFIX = "media:"


@dataclass(frozen=True, slots=True)
class TasteMix:
    """Shares (0..1, summing to 1) of positive taste per language and per format.

    Empty mappings mean "no signal": the corresponding cap is not applied.
    """

    languages: Mapping[str, float] = field(default_factory=dict)
    media: Mapping[str, float] = field(default_factory=dict)

    def as_json(self) -> dict[str, dict[str, float]]:
        return {
            "lang": {k: round(v, 4) for k, v in sorted(self.languages.items())},
            "media": {k: round(v, 4) for k, v in sorted(self.media.items())},
        }


def _strongest(snapshot: Mapping[str, Any] | None, prefix: str) -> str | None:
    best_key, best_weight = None, 0.0
    for key, value in (snapshot or {}).items():
        if not str(key).startswith(prefix):
            continue
        try:
            weight = float(value)
        except (TypeError, ValueError):
            continue
        if weight > best_weight:
            best_key, best_weight = str(key)[len(prefix) :], weight
    return best_key or None


def snapshot_language(snapshot: Mapping[str, Any] | None) -> str | None:
    """Original language (ISO 639-1) from a title's feature snapshot."""
    return _strongest(snapshot, LANG_PREFIX)


def snapshot_media(snapshot: Mapping[str, Any] | None) -> str | None:
    """``movie`` or ``tv`` from a title's feature snapshot."""
    return _strongest(snapshot, MEDIA_PREFIX)


def _shares(counter: Counter[str]) -> dict[str, float]:
    total = sum(v for v in counter.values() if v > 0)
    if total <= 0:
        return {}
    return {k: v / total for k, v in counter.items() if v > 0}


def taste_mix(weighted_snapshots: Iterable[tuple[Mapping[str, Any], float]]) -> TasteMix:
    """Mix from (feature snapshot, effective signal weight) pairs; negatives ignored."""
    languages: Counter[str] = Counter()
    media: Counter[str] = Counter()
    for snapshot, weight in weighted_snapshots:
        if weight <= 0:
            continue
        language = snapshot_language(snapshot)
        if language:
            languages[language] += weight
        fmt = snapshot_media(snapshot)
        if fmt:
            media[fmt] += weight
    return TasteMix(languages=_shares(languages), media=_shares(media))


def _clean_shares(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            share = float(value)
        except (TypeError, ValueError):
            continue
        if share > 0:
            out[str(key)] = share
    total = sum(out.values())
    return {k: v / total for k, v in out.items()} if total > 0 else {}


def read_taste_mix(features: Mapping[str, Any] | None) -> TasteMix:
    """The stored mix, or one derived from positive ``lang:`` / ``media:`` features."""
    features = features or {}
    stored = features.get(TASTE_MIX_KEY)
    if isinstance(stored, Mapping):
        return TasteMix(
            languages=_clean_shares(stored.get("lang")),
            media=_clean_shares(stored.get("media")),
        )
    languages: Counter[str] = Counter()
    media: Counter[str] = Counter()
    for key, value in features.items():
        key = str(key)
        try:
            weight = float(value)
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        if key.startswith(LANG_PREFIX):
            languages[key[len(LANG_PREFIX) :]] += weight
        elif key.startswith(MEDIA_PREFIX):
            media[key[len(MEDIA_PREFIX) :]] += weight
    return TasteMix(languages=_shares(languages), media=_shares(media))
