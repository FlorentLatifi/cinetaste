"""What people did with the picks they were shown.

Offline benchmarks only reward re-finding films someone already rated; they
cannot tell whether a hidden gem got saved or an exploration pick got passed.
Every For You slate is logged to ``recommendation_impressions`` and every
action to ``interaction_events``; this module joins the two.

Method
------
* One row per (user, title): the **first** time the title was shown in the
  period, with its position and reason codes. A title shown again on the next
  visit is the same recommendation, not a new chance to act.
* Its **outcome** is the first meaningful action on that title within
  ``window_days`` after it was shown (clears, skips and views are ignored;
  see ``OUTCOME_BY_EVENT``).
* Rates are shares of those rows, grouped by position band, by reason code and
  by the discovery badges. Groups smaller than ``min_group`` report a count but
  no rates — a 50% save rate from two impressions is not a finding.

Caveats, stated where the numbers are read: every card of a computed slate is
logged whether or not the person swiped that far, so later positions show
lower rates partly because fewer people saw them. Comparing configurations
means comparing date ranges (before / after a deploy); no ranker version is
stored on impressions. Only aggregates leave the database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.interaction import InteractionEvent, RecommendationImpression

Outcome = Literal["saved", "seen_liked", "seen_other", "passed"]

# Which actions count as an answer to a recommendation, and what kind.
#   saved       — wants to watch it: the discovery success.
#   seen_liked  — had already seen it and liked it: a right guess, not a find.
#   seen_other  — had seen it, no strong opinion.
#   passed      — rejected it.
# Event types missing here (clear, skip, view, haven't_seen) are not answers.
OUTCOME_BY_EVENT: dict[str, Outcome] = {
    "watchlist": "saved",
    "like": "seen_liked",
    "rate_2": "seen_liked",
    "rate_3": "seen_liked",
    "rate_4": "seen_liked",
    "watched_liked": "seen_liked",
    "mid": "seen_other",
    "watched": "seen_other",
    "not_interested": "passed",
    "dislike": "passed",
    "rate_1": "passed",
    "watched_disliked": "passed",
}
IGNORED_EVENTS = frozenset({"clear", "skip", "view", "haven't_seen"})

# Positions are 0-based in the log; bands are what a person scrolls through.
POSITION_BANDS: tuple[tuple[str, int, int], ...] = (
    ("1", 0, 0),
    ("2–3", 1, 2),
    ("4–10", 3, 9),
    ("11–20", 10, 19),
    ("21+", 20, 10_000),
)
DEFAULT_MIN_GROUP = 20


@dataclass(frozen=True, slots=True)
class ShownPick:
    """One title's first impression for one user, and what happened next."""

    position: int
    reason_codes: tuple[str, ...]
    outcome: Outcome | None


@dataclass(slots=True)
class GroupStats:
    shown: int = 0
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, pick: ShownPick) -> None:
        self.shown += 1
        if pick.outcome is not None:
            self.counts[pick.outcome] += 1

    def rates(self, min_group: int) -> dict[str, float] | None:
        """Share of shown picks per outcome; None when the group is too small."""
        if self.shown < max(min_group, 1):
            return None
        acted = sum(self.counts.values())
        good = self.counts["saved"] + self.counts["seen_liked"]
        return {
            "acted": acted / self.shown,
            "saved": self.counts["saved"] / self.shown,
            "seen_liked": self.counts["seen_liked"] / self.shown,
            "passed": self.counts["passed"] / self.shown,
            # Saved or already loved: the pick was right for this person.
            "good": good / self.shown,
            # Of the picks someone answered, how many were good.
            "good_of_acted": good / acted if acted else 0.0,
        }


@dataclass(slots=True)
class EngagementReport:
    since: datetime
    until: datetime
    window_days: int
    min_group: int
    overall: GroupStats
    by_position: dict[str, GroupStats]
    by_reason: dict[str, GroupStats]

    def as_dict(self) -> dict:
        def block(groups: dict[str, GroupStats]) -> dict:
            return {
                name: {"shown": g.shown, "rates": g.rates(self.min_group)}
                for name, g in groups.items()
            }

        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "window_days": self.window_days,
            "min_group": self.min_group,
            "overall": {"shown": self.overall.shown, "rates": self.overall.rates(self.min_group)},
            "by_position": block(self.by_position),
            "by_reason": block(self.by_reason),
        }


def position_band(position: int) -> str:
    for name, low, high in POSITION_BANDS:
        if low <= position <= high:
            return name
    return POSITION_BANDS[-1][0]


def summarize(
    picks: Iterable[ShownPick],
    *,
    since: datetime,
    until: datetime,
    window_days: int,
    min_group: int = DEFAULT_MIN_GROUP,
) -> EngagementReport:
    """Pure aggregation, so the arithmetic is testable without a database."""
    overall = GroupStats()
    by_position: dict[str, GroupStats] = {name: GroupStats() for name, _l, _h in POSITION_BANDS}
    by_reason: dict[str, GroupStats] = defaultdict(GroupStats)
    for pick in picks:
        overall.add(pick)
        by_position[position_band(pick.position)].add(pick)
        # A pick with several reasons counts toward each of them.
        for code in dict.fromkeys(pick.reason_codes):
            by_reason[code].add(pick)
    return EngagementReport(
        since=since,
        until=until,
        window_days=window_days,
        min_group=min_group,
        overall=overall,
        by_position={k: v for k, v in by_position.items() if v.shown},
        by_reason=dict(sorted(by_reason.items(), key=lambda kv: -kv[1].shown)),
    )


async def load_shown_picks(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    window_days: int,
) -> list[ShownPick]:
    """First impression per (user, title) in [since, until), with its outcome."""
    imp = RecommendationImpression
    first = (
        select(
            imp.user_id,
            imp.title_id,
            imp.position,
            imp.reason_codes,
            imp.created_at,
        )
        .distinct(imp.user_id, imp.title_id)
        .where(imp.created_at >= since, imp.created_at < until)
        .order_by(imp.user_id, imp.title_id, imp.created_at)
        .subquery("first_shown")
    )
    ev = InteractionEvent
    outcome = (
        select(ev.event_type)
        .where(
            ev.user_id == first.c.user_id,
            ev.title_id == first.c.title_id,
            ev.created_at > first.c.created_at,
            ev.created_at <= first.c.created_at + timedelta(days=window_days),
            ev.event_type.in_(list(OUTCOME_BY_EVENT)),
        )
        .order_by(ev.created_at)
        .limit(1)
        .correlate(first)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(first.c.position, first.c.reason_codes, outcome.label("event_type"))
        )
    ).all()
    return [
        ShownPick(
            position=int(row.position),
            reason_codes=tuple(str(c) for c in (row.reason_codes or [])),
            outcome=OUTCOME_BY_EVENT.get(row.event_type) if row.event_type else None,
        )
        for row in rows
    ]


def format_report(report: EngagementReport) -> str:
    """Markdown tables; rates as percentages, '—' where the group is too small."""

    def pct(rates: dict[str, float] | None, key: str) -> str:
        return "—" if rates is None else f"{rates[key] * 100:.1f}%"

    columns = ("acted", "saved", "seen_liked", "passed", "good", "good_of_acted")
    header = "| group | shown | " + " | ".join(columns) + " |"
    rule = "|" + "---|" * (len(columns) + 2)

    def table(title: str, groups: Sequence[tuple[str, GroupStats]]) -> list[str]:
        lines = [f"### {title}", "", header, rule]
        for name, stats in groups:
            rates = stats.rates(report.min_group)
            lines.append(
                f"| {name} | {stats.shown} | " + " | ".join(pct(rates, c) for c in columns) + " |"
            )
        return [*lines, ""]

    out = [
        f"## Engagement {report.since:%Y-%m-%d} → {report.until:%Y-%m-%d}",
        "",
        f"First impression per user and title; outcome = first answer within "
        f"{report.window_days} days. Groups under {report.min_group} shown picks report no rates.",
        "",
    ]
    out += table("Overall", [("all picks", report.overall)])
    out += table("By position", list(report.by_position.items()))
    out += table("By reason", list(report.by_reason.items()))
    return "\n".join(out)
