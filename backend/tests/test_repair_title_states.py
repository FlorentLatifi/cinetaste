"""The user_title_state repair plan (pure part; no database)."""

from __future__ import annotations

from uuid import uuid4

from app.scripts.repair_title_states import StateChange, plan_user_changes


def test_plan_fixes_rows_written_under_the_old_rule() -> None:
    user = uuid4()
    watch, watched, unseen, ok = uuid4(), uuid4(), uuid4(), uuid4()
    events = {
        watch: ["rate_4", "watchlist"],
        watched: ["rate_4", "watched"],
        unseen: ["rate_3", "haven't_seen"],
        ok: ["watchlist", "rate_3"],
    }
    stored = {watch: "watchlist", watched: "watched", unseen: "haven't_seen", ok: "like"}

    changes = plan_user_changes(user, events, stored)

    assert sorted(changes, key=lambda c: c.old or "") == sorted(
        [
            StateChange(user, unseen, "haven't_seen", "like"),
            StateChange(user, watch, "watchlist", "like"),
            StateChange(user, watched, "watched", "like"),
        ],
        key=lambda c: c.old or "",
    )


def test_plan_is_idempotent() -> None:
    user, t = uuid4(), uuid4()
    events = {t: ["rate_4", "watchlist"]}
    first = plan_user_changes(user, events, {t: "watchlist"})
    assert len(first) == 1
    assert plan_user_changes(user, events, {t: first[0].new}) == []


def test_plan_creates_missing_rows_only_for_real_states() -> None:
    user, rated, viewed = uuid4(), uuid4(), uuid4()
    changes = plan_user_changes(user, {rated: ["rate_3"], viewed: ["view"]}, {})
    assert changes == [StateChange(user, rated, None, "like")]


def test_plan_leaves_rows_without_events_alone() -> None:
    user, t = uuid4(), uuid4()
    assert plan_user_changes(user, {}, {t: "watchlist"}) == []
