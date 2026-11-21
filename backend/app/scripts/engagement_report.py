"""Online engagement with For You: action rate by slate position and reason code.

    python -m app.scripts.engagement_report                       # last 28 days
    python -m app.scripts.engagement_report --since 2026-12-01 --window-days 7
    python -m app.scripts.engagement_report --print-sql           # for psql

Offline metrics only reward re-finding titles a user already rated. This report
measures what users did with what they were *shown*, from
``recommendation_impressions`` (written once per computed slate) and
``interaction_events``.

Counting rules:

* **Unit** — a (user, title) pair, at its *first* impression in the period.
  A slate recomputed after a rating shows mostly the same titles again;
  counting every impression would inflate the denominator.
* **Acted** — the user's first *explicit* event on that title (an opinion or
  an intent in ``SIGNAL_POLICIES``: ratings, watchlist, like/dislike, not
  interested, watched) within ``--window-days`` after the impression.
  Implicit events (view, skip) and no-information events (haven't seen,
  clear) are not actions. **Positive** / **negative** follow the policy's
  polarity, so this report and the taste model agree on what a signal means.
* **Attribution is not causation** — an action on a title the user later found
  through search still counts. Rates are comparisons (position 1 vs 10, reason
  A vs B), not absolute truth. 95% Wilson intervals are shown: with an
  invite-only launch, most cells are small.

Safety: read-only and local-only. The session runs with
``default_transaction_read_only=on`` and the script refuses any database host
that is not local, with no override — run it against a restored backup
(docs/RUNBOOK.md, "Database backup and restore"), never against production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.domain.taste_signals import SIGNAL_POLICIES

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db", "postgres"})
DEFAULT_PERIOD_DAYS = 28
DEFAULT_WINDOW_DAYS = 7


def event_classes() -> dict[str, list[str]]:
    """Explicit events, split by polarity, straight from the signal policy table."""
    explicit = {
        name: policy
        for name, policy in SIGNAL_POLICIES.items()
        if policy.tier in {"opinion", "intent"}
    }
    return {
        "counted": sorted(explicit),
        "positive": sorted(n for n, p in explicit.items() if p.polarity == "positive"),
        "negative": sorted(n for n, p in explicit.items() if p.polarity == "negative"),
    }


_OUTCOMES = """
WITH shown AS (
    SELECT DISTINCT ON (ri.user_id, ri.title_id)
           ri.user_id, ri.title_id, ri.position, ri.reason_codes, ri.created_at
    FROM recommendation_impressions ri
    WHERE ri.created_at >= :since AND ri.created_at < :until
    ORDER BY ri.user_id, ri.title_id, ri.created_at
),
outcome AS (
    SELECT s.position,
           s.reason_codes,
           a.event_type,
           COALESCE(a.event_type = ANY(:positive), false) AS positive,
           COALESCE(a.event_type = ANY(:negative), false) AS negative
    FROM shown s
    LEFT JOIN LATERAL (
        SELECT ie.event_type
        FROM interaction_events ie
        WHERE ie.user_id = s.user_id
          AND ie.title_id = s.title_id
          AND ie.created_at >= s.created_at
          AND ie.created_at < s.created_at + make_interval(days => :window_days)
          AND ie.event_type = ANY(:counted)
        ORDER BY ie.created_at
        LIMIT 1
    ) a ON true
)
"""

_COUNTS = """
    count(*) AS shown,
    count(event_type) AS acted,
    count(*) FILTER (WHERE positive) AS positive,
    count(*) FILTER (WHERE negative) AS negative
"""

QUERIES: dict[str, str] = {
    "overview": _OUTCOMES + f"SELECT {_COUNTS} FROM outcome",
    "by_position": _OUTCOMES
    + f"SELECT position + 1 AS bucket, {_COUNTS} FROM outcome GROUP BY position ORDER BY position",
    # First reason = the one the card leads with.
    "by_primary_reason": _OUTCOMES
    + f"""SELECT COALESCE(reason_codes->>0, 'none') AS bucket, {_COUNTS}
    FROM outcome GROUP BY 1 ORDER BY shown DESC, bucket""",
    # Every reason on the card (an impression counts once per code it carried).
    "by_any_reason": _OUTCOMES
    + f"""SELECT code AS bucket, {_COUNTS}
    FROM outcome
    CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(reason_codes, '["none"]'::jsonb)) AS code
    GROUP BY code ORDER BY shown DESC, bucket""",
    "by_event": _OUTCOMES
    + f"""SELECT COALESCE(event_type, '(no action)') AS bucket, {_COUNTS}
    FROM outcome GROUP BY 1 ORDER BY shown DESC, bucket""",
    "volume": """
SELECT count(*) AS impressions,
       count(DISTINCT slate_id) AS slates,
       count(DISTINCT user_id) AS users
FROM recommendation_impressions
WHERE created_at >= :since AND created_at < :until
""",
}


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion (sane at small n and at 0 or 1)."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class EngagementReport:
    since: datetime
    until: datetime
    window_days: int
    volume: dict[str, int]
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "window_days": self.window_days,
            "volume": self.volume,
            "tables": self.tables,
        }


async def collect(
    conn: Any,
    *,
    since: datetime,
    until: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> EngagementReport:
    """Run every query on ``conn`` (an AsyncConnection or AsyncSession)."""
    params = {"since": since, "until": until, "window_days": window_days, **event_classes()}
    volume_row = (await conn.execute(text(QUERIES["volume"]), params)).mappings().one()
    report = EngagementReport(
        since=since, until=until, window_days=window_days, volume=dict(volume_row)
    )
    for name, sql in QUERIES.items():
        if name == "volume":
            continue
        rows = (await conn.execute(text(sql), params)).mappings().all()
        report.tables[name] = [_with_rates(dict(row)) for row in rows]
    return report


def _with_rates(row: dict[str, Any]) -> dict[str, Any]:
    shown = int(row["shown"])
    for key in ("acted", "positive", "negative"):
        count = int(row[key])
        row[f"{key}_rate"] = count / shown if shown else 0.0
        row[f"{key}_ci"] = wilson(count, shown)
    return row


def format_markdown(report: EngagementReport) -> str:
    v = report.volume
    lines = [
        f"# For You engagement {report.since:%Y-%m-%d} → {report.until:%Y-%m-%d}",
        "",
        f"{v['impressions']} impressions in {v['slates']} slates for {v['users']} users; "
        f"actions counted within {report.window_days} days of a title's first impression.",
    ]
    titles = {
        "overview": "Overall",
        "by_position": "By position (1 = first card)",
        "by_primary_reason": "By leading reason",
        "by_any_reason": "By any reason on the card",
        "by_event": "What the action was",
    }
    for name, heading in titles.items():
        rows = report.tables.get(name) or []
        lines += ["", f"## {heading}", ""]
        if not rows:
            lines.append("(no impressions in this period)")
            continue
        label = "bucket" if "bucket" in rows[0] else None
        header = ([label] if label else []) + ["shown", "acted", "positive", "negative"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        for row in rows:
            cells = [str(row[label])] if label else []
            cells.append(str(row["shown"]))
            for key in ("acted", "positive", "negative"):
                low, high = row[f"{key}_ci"]
                cells.append(f"{row[f'{key}_rate']:.1%} ({low:.0%}–{high:.0%})")
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def ensure_local_database(url: str) -> None:
    """Refuse anything but a local database: this report never touches production."""
    host = make_url(url).host
    if host and host not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing to connect to {host!r}: engagement_report runs against a local "
            "database only. Restore a backup locally (docs/RUNBOOK.md) and point "
            "DATABASE_URL at it."
        )


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="For You engagement by position and reason")
    parser.add_argument("--since", type=_date, help=f"Start date (default: {DEFAULT_PERIOD_DAYS} days ago)")
    parser.add_argument("--until", type=_date, help="End date, exclusive (default: now)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    parser.add_argument("--print-sql", action="store_true", help="Print the queries and exit")
    return parser


async def main(args: argparse.Namespace) -> int:
    if args.print_sql:
        classes = event_classes()
        print(f"-- :positive = {classes['positive']}\n-- :negative = {classes['negative']}")
        print(f"-- :counted  = {classes['counted']}")
        for name, sql in QUERIES.items():
            print(f"\n-- {name}\n{sql.strip()};")
        return 0

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    url = get_settings().database_url
    ensure_local_database(url)
    until = args.until or datetime.now(UTC)
    since = args.since or until - timedelta(days=DEFAULT_PERIOD_DAYS)
    engine = create_async_engine(
        url,
        connect_args={
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "120000",
                "application_name": "cinetaste-engagement-report",
            }
        },
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            report = await collect(conn, since=since, until=until, window_days=args.window_days)
    finally:
        await engine.dispose()
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, default=str))
    else:
        print(format_markdown(report))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(build_parser().parse_args())))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
