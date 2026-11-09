"""Engagement aggregation: the arithmetic and the event classification."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.engagement import (
    IGNORED_EVENTS,
    OUTCOME_BY_EVENT,
    ShownPick,
    format_report,
    position_band,
    summarize,
)
from app.domain.taste_signals import SIGNAL_POLICIES

SINCE = datetime(2026, 11, 1, tzinfo=UTC)
UNTIL = datetime(2026, 12, 1, tzinfo=UTC)


def test_every_event_type_is_classified() -> None:
    """A new event type must be placed deliberately, not silently ignored."""
    classified = set(OUTCOME_BY_EVENT) | IGNORED_EVENTS
    assert set(SIGNAL_POLICIES) == classified
    assert not set(OUTCOME_BY_EVENT) & IGNORED_EVENTS


@pytest.mark.parametrize(
    ("position", "band"), [(0, "1"), (1, "2–3"), (2, "2–3"), (9, "4–10"), (19, "11–20"), (25, "21+")]
)
def test_position_bands(position: int, band: str) -> None:
    assert position_band(position) == band


def _report(picks, min_group=1):
    return summarize(picks, since=SINCE, until=UNTIL, window_days=7, min_group=min_group)


def test_rates_are_shares_of_shown_picks() -> None:
    picks = [
        ShownPick(0, ("because_you_liked",), "saved"),
        ShownPick(1, ("because_you_liked", "hidden_gem"), "seen_liked"),
        ShownPick(2, ("hidden_gem",), "passed"),
        ShownPick(3, ("discovery",), None),
    ]
    report = _report(picks)
    rates = report.overall.rates(1)
    assert report.overall.shown == 4
    assert rates["acted"] == 0.75
    assert rates["saved"] == 0.25
    assert rates["good"] == 0.5
    assert rates["good_of_acted"] == pytest.approx(2 / 3)
    # A pick with two reasons counts toward both.
    assert report.by_reason["hidden_gem"].shown == 2
    assert report.by_reason["hidden_gem"].rates(1)["passed"] == 0.5
    assert report.by_position["2–3"].shown == 2


def test_small_groups_report_counts_but_no_rates() -> None:
    picks = [ShownPick(0, ("discovery",), "saved")] * 3 + [ShownPick(0, ("other",), None)] * 30
    report = _report(picks, min_group=20)
    assert report.by_reason["discovery"].rates(20) is None
    assert report.by_reason["other"].rates(20) is not None
    text = format_report(report)
    assert "| discovery | 3 | — |" in text
    assert report.as_dict()["by_reason"]["discovery"] == {"shown": 3, "rates": None}


def test_no_impressions_is_an_empty_report_not_an_error() -> None:
    report = _report([], min_group=20)
    assert report.overall.shown == 0
    assert report.overall.rates(20) is None
    assert "all picks | 0 |" in format_report(report)
