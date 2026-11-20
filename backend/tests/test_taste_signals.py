"""Unit tests for the canonical taste signal policy."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.application.onboarding_service import (
    _LEGACY_ACTION_MAP,
    MIN_ONBOARDING_POSITIVE,
    MIN_ONBOARDING_RATINGS,
    ONBOARDING_ACTIONS,
)
from app.domain.taste_signals import (
    ACTIVE_INTERACTION_EVENT_TYPES,
    EXPLAIN_ANCHOR_EVENT_TYPES,
    EXPLAIN_ANCHOR_MIN_WEIGHT,
    FEED_EXCLUDE_STATES,
    FUTURE_INTERACTION_EVENT_TYPES,
    HISTORY_VISIBLE_STATES,
    POSITIVE_RATING_EVENT_TYPES,
    RATING_EVENT_TYPES,
    SIGNAL_POLICIES,
    SIGNAL_WEIGHTS,
    STATE_FROM_EVENT,
    ZERO_SIGNAL_EPS,
    affects_taste,
    effective_title_signals,
    get_policy,
    is_superseded_by_clear,
    label_for_state,
    last_clear_timestamps,
    policy_table_markdown,
    resolve_title_state,
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


def test_history_states_and_labels() -> None:
    assert "like" in HISTORY_VISIBLE_STATES
    assert "watched" in HISTORY_VISIBLE_STATES
    assert "none" not in HISTORY_VISIBLE_STATES
    assert "haven't_seen" not in HISTORY_VISIBLE_STATES
    assert label_for_state("like") == "Liked"
    assert label_for_state("not_interested") == "Not interested"
    assert label_for_state("custom_x") == "Custom X"
    # API filter allow-list matches UI chips
    for s in ("like", "dislike", "watchlist", "not_interested", "rated", "watched"):
        assert s in HISTORY_VISIBLE_STATES


def test_clear_is_zero_signal_and_active_api() -> None:
    p = get_policy("clear")
    assert p.weight == 0.0
    assert p.updates_taste is False
    assert not affects_taste("clear")
    assert p.user_title_state == "none"
    assert p.exclude_from_feed is False
    assert "none" not in FEED_EXCLUDE_STATES
    assert "clear" in ACTIVE_INTERACTION_EVENT_TYPES
    assert STATE_FROM_EVENT["clear"] == "none"


def test_clear_supersedes_prior_events_for_title() -> None:
    t1, t2 = uuid4(), uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        (t1, "like", t0),
        (t1, "dislike", t0 + timedelta(minutes=1)),
        (t2, "like", t0 + timedelta(minutes=2)),
        (t1, "clear", t0 + timedelta(minutes=3)),
        (t1, "watchlist", t0 + timedelta(minutes=4)),
    ]
    last_clear = last_clear_timestamps(events)
    assert t1 in last_clear
    assert t2 not in last_clear

    assert is_superseded_by_clear(
        title_id=t1, event_type="like", created_at=t0, last_clear=last_clear
    )
    assert is_superseded_by_clear(
        title_id=t1,
        event_type="dislike",
        created_at=t0 + timedelta(minutes=1),
        last_clear=last_clear,
    )
    assert is_superseded_by_clear(
        title_id=t1,
        event_type="clear",
        created_at=t0 + timedelta(minutes=3),
        last_clear=last_clear,
    )
    assert not is_superseded_by_clear(
        title_id=t1,
        event_type="watchlist",
        created_at=t0 + timedelta(minutes=4),
        last_clear=last_clear,
    )
    assert not is_superseded_by_clear(
        title_id=t2,
        event_type="like",
        created_at=t0 + timedelta(minutes=2),
        last_clear=last_clear,
    )


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
    assert RATING_EVENT_TYPES == {"mid", "rate_1", "rate_2", "rate_3", "rate_4"}
    assert "haven't_seen" not in RATING_EVENT_TYPES
    assert "not_interested" not in RATING_EVENT_TYPES
    assert POSITIVE_RATING_EVENT_TYPES >= {"rate_2", "rate_3", "rate_4", "like"}
    assert EXPLAIN_ANCHOR_EVENT_TYPES >= {"rate_3", "rate_4", "like"}
    assert "rate_2" not in EXPLAIN_ANCHOR_EVENT_TYPES
    assert EXPLAIN_ANCHOR_MIN_WEIGHT >= 0.85


def test_watched_actions_are_active_api() -> None:
    assert FUTURE_INTERACTION_EVENT_TYPES == set()
    for name in ("watched", "watched_liked", "watched_disliked"):
        p = get_policy(name)
        assert p.updates_taste
        assert name in SIGNAL_WEIGHTS
        assert name in ACTIVE_INTERACTION_EVENT_TYPES
        assert p.exclude_from_feed is True
    assert weight_for("watched") > 0
    assert weight_for("watched") < weight_for("watchlist")
    assert weight_for("watched_liked") > weight_for("rate_3")
    assert weight_for("watched_disliked") < 0
    assert STATE_FROM_EVENT["watched"] == "watched"
    assert "watched" in FEED_EXCLUDE_STATES


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


# ---------------------------------------------------------------------------
# Durable user_title_state: resolve_title_state
# ---------------------------------------------------------------------------


def _state(*events: str) -> tuple[str, str | None]:
    r = resolve_title_state(events)
    return r.state, r.rating


def test_state_watchlist_after_rating_keeps_the_rating() -> None:
    assert _state("rate_4", "watchlist") == ("like", "rate_4")


def test_state_watched_after_rating_keeps_the_rating() -> None:
    assert _state("rate_4", "watched") == ("like", "rate_4")


def test_state_havent_seen_never_replaces_an_opinion_or_intent() -> None:
    assert _state("rate_3", "haven't_seen") == ("like", "rate_3")
    assert _state("watchlist", "haven't_seen") == ("watchlist", None)
    assert _state("not_interested", "haven't_seen") == ("not_interested", None)
    # A rated title must stay out of For You.
    assert _state("rate_3", "haven't_seen")[0] in FEED_EXCLUDE_STATES


def test_state_rating_replaces_watchlist() -> None:
    assert _state("watchlist", "rate_3") == ("like", "rate_3")


def test_state_rating_after_clear_applies() -> None:
    assert _state("rate_4", "clear", "rate_1") == ("dislike", "rate_1")


def test_state_clear_resets_everything_before_it() -> None:
    # The SPA no longer sends this for a rated title; History "Clear" still does.
    assert _state("rate_4", "watchlist", "clear") == ("none", None)
    assert _state("clear") == ("none", None)
    assert _state() == ("none", None)


def test_state_later_opinion_replaces_earlier_opinion() -> None:
    assert _state("rate_4", "rate_1") == ("dislike", "rate_1")
    assert _state("rate_4", "not_interested") == ("not_interested", None)
    assert _state("not_interested", "rate_3") == ("like", "rate_3")
    assert _state("rate_2", "mid") == ("rated", "mid")


def test_state_latest_intent_wins_among_intents() -> None:
    assert _state("watchlist", "watched") == ("watched", None)
    assert _state("watched", "watchlist") == ("watchlist", None)


def test_state_implicit_events_set_no_state() -> None:
    assert _state("view", "skip") == ("none", None)
    assert _state("watchlist", "view", "skip") == ("watchlist", None)
    assert _state("haven't_seen", "view") == ("haven't_seen", None)


def test_state_shortcuts_are_opinions_without_a_rating() -> None:
    assert _state("rate_4", "dislike") == ("dislike", None)
    assert _state("watched_liked", "watchlist") == ("like", None)


def test_state_ignores_unknown_legacy_events() -> None:
    assert _state("rate_3", "some_retired_event") == ("like", "rate_3")


def test_state_matches_policy_for_every_single_event() -> None:
    for name, policy in SIGNAL_POLICIES.items():
        expected = policy.user_title_state or "none"
        assert resolve_title_state([name]).state == expected, name


def test_state_agrees_with_effective_signal() -> None:
    """The stored state reflects the same event the profile learns from."""
    t = uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    sequences = [
        ["rate_4", "watchlist"],
        ["rate_4", "watched"],
        ["rate_3", "haven't_seen"],
        ["watchlist", "rate_3"],
        ["rate_4", "clear", "rate_1"],
        ["like", "dislike"],
        ["watchlist", "watched"],
    ]
    for seq in sequences:
        events = [
            (t, e, SIGNAL_WEIGHTS[e], t0 + timedelta(minutes=i)) for i, e in enumerate(seq)
        ]
        effective = effective_title_signals(events)[t].event_type
        assert resolve_title_state(seq).state == STATE_FROM_EVENT[effective], seq
