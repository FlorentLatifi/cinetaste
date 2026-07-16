"""Canonical taste-signal policy for CineTaste.

This module is the **single source of truth** for how user actions affect:

* sparse taste features (genre / people / keywords / tone / …)
* dense taste vector (blended title embeddings)
* ``user_title_state`` (feed filters, watchlist)
* explain anchors ("because you liked X")

Product-facing write-up: ``docs/TASTE_SIGNALS.md``.

Rules of thumb
--------------
1. **Seen + opinion** (ratings) → strongest learning.
2. **Intent without quality** (watchlist) → mild positive only.
3. **Rejection without depth** (not interested) → mild negative.
4. **No familiarity** (haven't seen) → **zero** taste signal; still log for UX.
5. **Implicit** (view/skip) → tiny or near-zero; never dominate explicit ratings.
6. Events with ``abs(weight) < ZERO_SIGNAL_EPS`` never touch the profile on recompute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal
from uuid import UUID

Polarity = Literal["positive", "negative", "neutral", "zero"]

# Absolute weight below this is treated as no taste influence on recompute.
ZERO_SIGNAL_EPS = 1e-9

# Minimum stored event weight to cite a title in "because you liked …" reasons.
EXPLAIN_ANCHOR_MIN_WEIGHT = 0.85


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    """Policy row for one ``event_type`` stored on InteractionEvent."""

    event_type: str
    weight: float
    polarity: Polarity
    # If False, recompute skips this event even if weight were non-zero.
    updates_taste: bool
    # Resulting UserTitleState.state, or None to leave state as "none"/unchanged mapping.
    user_title_state: str | None
    # Hide from For You candidate generation while in this state.
    exclude_from_feed: bool
    # Explicit 1–4 style rating (onboarding "real ratings" counter).
    counts_as_rating: bool
    # Positive affinity for onboarding gates / mild-positive set.
    counts_as_positive_rating: bool
    # Eligible to become an explain-memory anchor when weight is high enough.
    explain_anchor_eligible: bool
    label: str
    summary: str
    special_handling: str


def _p(
    event_type: str,
    weight: float,
    polarity: Polarity,
    *,
    updates_taste: bool | None = None,
    state: str | None,
    exclude_from_feed: bool,
    counts_as_rating: bool = False,
    counts_as_positive_rating: bool = False,
    explain_anchor_eligible: bool = False,
    label: str,
    summary: str,
    special_handling: str = "",
) -> SignalPolicy:
    affects = updates_taste if updates_taste is not None else abs(weight) >= ZERO_SIGNAL_EPS
    return SignalPolicy(
        event_type=event_type,
        weight=weight,
        polarity=polarity,
        updates_taste=affects,
        user_title_state=state,
        exclude_from_feed=exclude_from_feed,
        counts_as_rating=counts_as_rating,
        counts_as_positive_rating=counts_as_positive_rating,
        explain_anchor_eligible=explain_anchor_eligible,
        label=label,
        summary=summary,
        special_handling=special_handling,
    )


# ---------------------------------------------------------------------------
# Active + reserved signal table
# ---------------------------------------------------------------------------

SIGNAL_POLICIES: dict[str, SignalPolicy] = {
    # --- Explicit ratings (user has seen the title) -------------------------
    "rate_1": _p(
        "rate_1",
        -0.90,
        "negative",
        state="dislike",
        exclude_from_feed=True,
        counts_as_rating=True,
        label="Bad",
        summary="Strong negative: user saw it and disliked it.",
        special_handling="Pushes away shared features (genres, people, tone). Not an explain anchor.",
    ),
    "rate_2": _p(
        "rate_2",
        0.30,
        "positive",
        state="rated",
        exclude_from_feed=True,
        counts_as_rating=True,
        counts_as_positive_rating=True,
        label="It's ok",
        summary="Weak positive: familiar but not a favorite.",
        special_handling="Too weak alone for explain anchors; still moves sparse features lightly.",
    ),
    "rate_3": _p(
        "rate_3",
        1.00,
        "positive",
        state="like",
        exclude_from_feed=True,
        counts_as_rating=True,
        counts_as_positive_rating=True,
        explain_anchor_eligible=True,
        label="Good",
        summary="Solid positive rating of a seen title.",
        special_handling="Eligible explain anchor; primary learning signal with rate_4.",
    ),
    "rate_4": _p(
        "rate_4",
        1.55,
        "positive",
        state="like",
        exclude_from_feed=True,
        counts_as_rating=True,
        counts_as_positive_rating=True,
        explain_anchor_eligible=True,
        label="Favorite",
        summary="Strongest positive explicit rating.",
        special_handling="Highest boost to matching features/embeddings; preferred explain anchor.",
    ),
    # --- Onboarding / preference without a numeric rating -------------------
    "haven't_seen": _p(
        "haven't_seen",
        0.0,
        "zero",
        updates_taste=False,
        state="haven't_seen",
        exclude_from_feed=False,
        label="Haven't seen it",
        summary="Zero taste signal — unfamiliarity is not dislike.",
        special_handling=(
            "Logged for analytics/onboarding progress only. "
            "Does NOT update sparse features or the taste vector. "
            "Does NOT exclude from For You (user may still enjoy a recommendation)."
        ),
    ),
    "not_interested": _p(
        "not_interested",
        -0.40,
        "negative",
        state="not_interested",
        exclude_from_feed=True,
        label="Not interested",
        summary="Mild negative rejection (knows the vibe / refuses the title).",
        special_handling=(
            "Weaker than Bad (rate_1). Use when the user rejects without a full rating scale. "
            "Excluded from For You."
        ),
    ),
    # --- Intent & feed shortcuts --------------------------------------------
    "watchlist": _p(
        "watchlist",
        0.45,
        "positive",
        state="watchlist",
        exclude_from_feed=True,
        label="Watchlist / Save",
        summary="Mild positive intent to watch later — not a quality judgment.",
        special_handling=(
            "Weaker than Good. Signals curiosity/affinity, not 'I loved this'. "
            "Excluded from For You while saved (avoid re-recommending the same card)."
        ),
    ),
    "like": _p(
        "like",
        1.00,
        "positive",
        state="like",
        exclude_from_feed=True,
        counts_as_positive_rating=True,
        explain_anchor_eligible=True,
        label="Like (shortcut)",
        summary="Feed shortcut equivalent to Good (rate_3).",
        special_handling="Prefer rate_* in onboarding; like remains for post-onboarding UI.",
    ),
    "dislike": _p(
        "dislike",
        -0.85,
        "negative",
        state="dislike",
        exclude_from_feed=True,
        label="Dislike / Pass (shortcut)",
        summary="Feed shortcut ≈ Bad (rate_1), slightly softer.",
        special_handling="Prefer rate_1 when the user is on an explicit rating scale.",
    ),
    # --- Implicit / low-confidence ------------------------------------------
    "clear": _p(
        "clear",
        0.0,
        "zero",
        updates_taste=False,
        state="none",
        exclude_from_feed=False,
        label="Undo / clear",
        summary="Revert the user–title relationship so the title can reappear on For You.",
        special_handling=(
            "Sets state to none (not excluded). Recompute ignores all prior events "
            "for that title_id up to and including this clear (append-only undo)."
        ),
    ),
    "skip": _p(
        "skip",
        -0.15,
        "negative",
        state=None,
        exclude_from_feed=False,
        label="Skip",
        summary="Tiny negative from dismissing a card without a firm opinion.",
        special_handling="Does not set a durable title state; barely moves profile.",
    ),
    "view": _p(
        "view",
        0.05,
        "neutral",
        state=None,
        exclude_from_feed=False,
        label="View / impression",
        summary="Near-zero positive for analytics; almost no learning.",
        special_handling="Optional impression log; never treat as a real like.",
    ),
    # --- Reserved future actions (API may enable later) ---------------------
    "watched": _p(
        "watched",
        0.20,
        "positive",
        state="watched",
        exclude_from_feed=True,
        label="Watched (future)",
        summary="Marked as watched without a quality rating — weak positive completion signal.",
        special_handling=(
            "Reserved. Prefer prompting for rate_1–4 after watch. "
            "Excludes from For You to avoid re-serving finished titles."
        ),
    ),
    "watched_liked": _p(
        "watched_liked",
        1.10,
        "positive",
        state="like",
        exclude_from_feed=True,
        counts_as_positive_rating=True,
        explain_anchor_eligible=True,
        label="Watched + liked (future)",
        summary="Completed and enjoyed — slightly above Good.",
        special_handling="Reserved convenience event; equivalent to a strong post-watch like.",
    ),
    "watched_disliked": _p(
        "watched_disliked",
        -0.95,
        "negative",
        state="dislike",
        exclude_from_feed=True,
        label="Watched + disliked (future)",
        summary="Completed and disliked — strong negative, similar to Bad.",
        special_handling="Reserved. Stronger than not_interested because they invested time.",
    ),
}


# ---------------------------------------------------------------------------
# Derived maps used by services (always built from SIGNAL_POLICIES)
# ---------------------------------------------------------------------------

SIGNAL_WEIGHTS: dict[str, float] = {
    name: policy.weight for name, policy in SIGNAL_POLICIES.items()
}

STATE_FROM_EVENT: dict[str, str] = {
    name: policy.user_title_state
    for name, policy in SIGNAL_POLICIES.items()
    if policy.user_title_state is not None
}

FEED_EXCLUDE_STATES: frozenset[str] = frozenset(
    policy.user_title_state
    for policy in SIGNAL_POLICIES.values()
    if policy.exclude_from_feed and policy.user_title_state is not None
)

RATING_EVENT_TYPES: frozenset[str] = frozenset(
    name for name, policy in SIGNAL_POLICIES.items() if policy.counts_as_rating
)

POSITIVE_RATING_EVENT_TYPES: frozenset[str] = frozenset(
    name for name, policy in SIGNAL_POLICIES.items() if policy.counts_as_positive_rating
)

EXPLAIN_ANCHOR_EVENT_TYPES: frozenset[str] = frozenset(
    name for name, policy in SIGNAL_POLICIES.items() if policy.explain_anchor_eligible
)

# Events accepted by the public interactions API today (exclude pure "future" until UI ships).
ACTIVE_INTERACTION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "like",
        "dislike",
        "watchlist",
        "not_interested",
        "clear",
        "skip",
        "view",
        "haven't_seen",
        "rate_1",
        "rate_2",
        "rate_3",
        "rate_4",
    }
)

# Reserved names documented for future UI; still learnable if recorded.
FUTURE_INTERACTION_EVENT_TYPES: frozenset[str] = frozenset(
    {"watched", "watched_liked", "watched_disliked"}
)


def get_policy(event_type: str) -> SignalPolicy:
    try:
        return SIGNAL_POLICIES[event_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported event_type: {event_type}") from exc


def is_supported_event(event_type: str) -> bool:
    return event_type in SIGNAL_POLICIES


def affects_taste(event_type: str, weight: float | None = None) -> bool:
    """Whether this event should move sparse features / embeddings on recompute."""
    policy = get_policy(event_type)
    if not policy.updates_taste:
        return False
    w = policy.weight if weight is None else float(weight)
    return abs(w) >= ZERO_SIGNAL_EPS


def weight_for(event_type: str) -> float:
    return get_policy(event_type).weight


def last_clear_timestamps(
    events: Iterable[tuple[UUID, str, datetime]],
) -> dict[UUID, datetime]:
    """Map title_id → created_at of the latest ``clear`` event for that title."""
    out: dict[UUID, datetime] = {}
    for title_id, event_type, created_at in events:
        if event_type == "clear":
            prev = out.get(title_id)
            if prev is None or created_at >= prev:
                out[title_id] = created_at
    return out


def is_superseded_by_clear(
    *,
    title_id: UUID,
    event_type: str,
    created_at: datetime,
    last_clear: dict[UUID, datetime],
) -> bool:
    """True if this event should not affect taste (cleared or is the clear itself)."""
    if event_type == "clear":
        return True
    cutoff = last_clear.get(title_id)
    if cutoff is None:
        return False
    return created_at <= cutoff


def policy_table_markdown() -> str:
    """Render the policy table (used by docs / debugging)."""
    lines = [
        "| event_type | label | weight | polarity | updates taste? | state | exclude For You? | notes |",
        "|------------|-------|--------|----------|----------------|-------|------------------|-------|",
    ]
    for name in sorted(SIGNAL_POLICIES.keys()):
        p = SIGNAL_POLICIES[name]
        lines.append(
            f"| `{p.event_type}` | {p.label} | {p.weight:+.2f} | {p.polarity} | "
            f"{'yes' if p.updates_taste else '**no**'} | "
            f"{p.user_title_state or '—'} | "
            f"{'yes' if p.exclude_from_feed else 'no'} | "
            f"{p.special_handling or p.summary} |"
        )
    return "\n".join(lines)
