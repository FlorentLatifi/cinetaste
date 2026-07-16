"""Unit tests for the canonical taste signal policy."""

from app.application.onboarding_service import (
    MIN_ONBOARDING_POSITIVE,
    MIN_ONBOARDING_RATINGS,
    ONBOARDING_ACTIONS,
    _LEGACY_ACTION_MAP,
)
from app.domain.taste_signals import (
    ACTIVE_INTERACTION_EVENT_TYPES,
    EXPLAIN_ANCHOR_EVENT_TYPES,
    EXPLAIN_ANCHOR_MIN_WEIGHT,
    FEED_EXCLUDE_STATES,
    FUTURE_INTERACTION_EVENT_TYPES,
    POSITIVE_RATING_EVENT_TYPES,
    RATING_EVENT_TYPES,
    SIGNAL_POLICIES,
    SIGNAL_WEIGHTS,
    STATE_FROM_EVENT,
    ZERO_SIGNAL_EPS,
    affects_taste,
    get_policy,
    policy_table_markdown,
    weight_for,
)
from app.recommendation.embeddings import blend_vectors


def test_havent_seen_is_zero_signal() -> None:
    p = get_policy("haven't_seen")
    assert p.weight == 0.0
    assert p.polarity == "zero"
    assert p.updates_taste is False
    assert not affects_taste("haven't_seen")
    assert not affects_taste("haven't_seen", 0.0)
    assert p.user_title_state == "haven't_seen"
    assert p.exclude_from_feed is False
    assert "haven't_seen" not in FEED_EXCLUDE_STATES
    assert abs(SIGNAL_WEIGHTS["haven't_seen"]) < ZERO_SIGNAL_EPS


def test_not_interested_is_mild_negative() -> None:
    p = get_policy("not_interested")
    assert p.weight < 0
    assert p.weight > weight_for("rate_1")  # milder than Bad
    assert p.weight > -0.7
    assert p.updates_taste is True
    assert p.exclude_from_feed is True
    assert STATE_FROM_EVENT["not_interested"] == "not_interested"
    assert "not_interested" in FEED_EXCLUDE_STATES


def test_rating_scale_monotonic_and_documented() -> None:
    assert weight_for("rate_1") < weight_for("rate_2")
    assert weight_for("rate_2") < weight_for("rate_3")
    assert weight_for("rate_3") < weight_for("rate_4")
    assert weight_for("rate_1") < 0
    assert weight_for("rate_2") > 0
    assert weight_for("rate_4") > weight_for("like")
    assert get_policy("rate_1").label == "Bad"
    assert get_policy("rate_4").label == "Favorite"
    for code in ("rate_1", "rate_2", "rate_3", "rate_4"):
        assert get_policy(code).counts_as_rating


def test_watchlist_is_mild_positive_intent() -> None:
    p = get_policy("watchlist")
    assert 0 < p.weight < weight_for("rate_3")
    assert p.polarity == "positive"
    assert p.exclude_from_feed is True
    assert p.user_title_state == "watchlist"
    assert not p.counts_as_rating
    assert not p.explain_anchor_eligible


def test_like_dislike_shortcuts_align_with_ratings() -> None:
    assert weight_for("like") == weight_for("rate_3")
    assert abs(weight_for("dislike") - weight_for("rate_1")) < 0.1
    assert get_policy("like").explain_anchor_eligible
    assert not get_policy("dislike").explain_anchor_eligible


def test_rating_and_anchor_sets() -> None:
    assert RATING_EVENT_TYPES == {"rate_1", "rate_2", "rate_3", "rate_4"}
    assert "haven't_seen" not in RATING_EVENT_TYPES
    assert "not_interested" not in RATING_EVENT_TYPES
    assert POSITIVE_RATING_EVENT_TYPES >= {"rate_2", "rate_3", "rate_4", "like"}
    assert EXPLAIN_ANCHOR_EVENT_TYPES >= {"rate_3", "rate_4", "like"}
    assert "rate_2" not in EXPLAIN_ANCHOR_EVENT_TYPES
    assert EXPLAIN_ANCHOR_MIN_WEIGHT >= 0.85


def test_future_watched_actions_reserved() -> None:
    assert FUTURE_INTERACTION_EVENT_TYPES == {
        "watched",
        "watched_liked",
        "watched_disliked",
    }
    for name in FUTURE_INTERACTION_EVENT_TYPES:
        p = get_policy(name)
        assert p.updates_taste
        assert name in SIGNAL_WEIGHTS
        # Not exposed on the public interaction API yet
        assert name not in ACTIVE_INTERACTION_EVENT_TYPES
    assert weight_for("watched") > 0
    assert weight_for("watched") < weight_for("watchlist")
    assert weight_for("watched_liked") > weight_for("rate_3")
    assert weight_for("watched_disliked") < 0


def test_feed_excludes_policy_states_not_unseen() -> None:
    for state in ("like", "dislike", "not_interested", "watchlist", "rated", "watched"):
        assert state in FEED_EXCLUDE_STATES
    assert "haven't_seen" not in FEED_EXCLUDE_STATES
    assert "none" not in FEED_EXCLUDE_STATES


def test_onboarding_gates_and_actions() -> None:
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
    assert blend_vectors([(unit, 0.0), (unit, 0.0)]) is None
    blended = blend_vectors([(unit, 1.0)])
    assert blended is not None
    assert abs(blended[0] - 1.0) < 1e-6


def test_policy_table_renders() -> None:
    md = policy_table_markdown()
    assert "haven't_seen" in md
    assert "rate_4" in md
    assert len(SIGNAL_POLICIES) >= 12


def test_signal_weights_match_policies() -> None:
    for name, policy in SIGNAL_POLICIES.items():
        assert SIGNAL_WEIGHTS[name] == policy.weight
        if policy.user_title_state:
            assert STATE_FROM_EVENT[name] == policy.user_title_state
