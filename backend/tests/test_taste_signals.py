"""Unit tests for onboarding / taste signal semantics (no DB)."""

from app.application.onboarding_service import (
    MIN_ONBOARDING_POSITIVE,
    MIN_ONBOARDING_RATINGS,
    ONBOARDING_ACTIONS,
    _LEGACY_ACTION_MAP,
)
from app.application.taste_service import (
    FEED_EXCLUDE_STATES,
    POSITIVE_RATING_EVENT_TYPES,
    RATING_EVENT_TYPES,
    SIGNAL_WEIGHTS,
    STATE_FROM_EVENT,
    ZERO_SIGNAL_EPS,
)
from app.recommendation.embeddings import blend_vectors


def test_havent_seen_is_zero_signal() -> None:
    assert "haven't_seen" in SIGNAL_WEIGHTS
    assert abs(SIGNAL_WEIGHTS["haven't_seen"]) < ZERO_SIGNAL_EPS
    assert STATE_FROM_EVENT["haven't_seen"] == "haven't_seen"
    # Must never suppress For You recommendations
    assert "haven't_seen" not in FEED_EXCLUDE_STATES


def test_not_interested_is_mild_negative() -> None:
    w = SIGNAL_WEIGHTS["not_interested"]
    assert w < 0
    assert w > SIGNAL_WEIGHTS["rate_1"]  # milder than Bad
    assert w > -0.7
    assert STATE_FROM_EVENT["not_interested"] == "not_interested"
    assert "not_interested" in FEED_EXCLUDE_STATES


def test_rating_scale_monotonic() -> None:
    assert SIGNAL_WEIGHTS["rate_1"] < SIGNAL_WEIGHTS["rate_2"]
    assert SIGNAL_WEIGHTS["rate_2"] < SIGNAL_WEIGHTS["rate_3"]
    assert SIGNAL_WEIGHTS["rate_3"] < SIGNAL_WEIGHTS["rate_4"]
    assert SIGNAL_WEIGHTS["rate_1"] < 0
    assert SIGNAL_WEIGHTS["rate_2"] > 0
    assert SIGNAL_WEIGHTS["rate_4"] > SIGNAL_WEIGHTS["like"]


def test_rating_event_sets() -> None:
    assert RATING_EVENT_TYPES == {"rate_1", "rate_2", "rate_3", "rate_4"}
    assert "haven't_seen" not in RATING_EVENT_TYPES
    assert "not_interested" not in RATING_EVENT_TYPES
    assert POSITIVE_RATING_EVENT_TYPES >= {"rate_2", "rate_3", "rate_4"}


def test_onboarding_gates() -> None:
    assert MIN_ONBOARDING_RATINGS >= 6
    assert MIN_ONBOARDING_POSITIVE >= 2
    assert "haven't_seen" in ONBOARDING_ACTIONS
    assert "not_interested" in ONBOARDING_ACTIONS
    for a in ("rate_1", "rate_2", "rate_3", "rate_4"):
        assert a in ONBOARDING_ACTIONS
    assert _LEGACY_ACTION_MAP["like"] == "rate_3"
    assert _LEGACY_ACTION_MAP["dislike"] == "rate_1"


def test_zero_weight_vectors_do_not_blend() -> None:
    unit = [1.0] + [0.0] * 383
    # Only zero-weight contributions → no vector
    assert blend_vectors([(unit, 0.0), (unit, 0.0)]) is None
    # Positive weight still works
    blended = blend_vectors([(unit, 1.0)])
    assert blended is not None
    assert abs(blended[0] - 1.0) < 1e-6


def test_feed_excludes_rated_and_not_interested_not_unseen() -> None:
    for state in ("like", "dislike", "not_interested", "watchlist", "rated"):
        assert state in FEED_EXCLUDE_STATES
    assert "haven't_seen" not in FEED_EXCLUDE_STATES
    assert "none" not in FEED_EXCLUDE_STATES
