"""What people did with their recommendations — save, pass, already loved.

    python -m app.scripts.engagement_report                       # last 30 days
    python -m app.scripts.engagement_report --since 2026-11-01 --until 2026-12-01
    python -m app.scripts.engagement_report --json report.json

Read-only. Prints Markdown with aggregates only (no user ids). To compare two
ranking configurations, run it for the date ranges before and after the
deploy. Method and caveats: app/application/engagement.py, docs/EVALUATION.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.application.engagement import (
    DEFAULT_MIN_GROUP,
    format_report,
    load_shown_picks,
    summarize,
)
from app.infrastructure.db.session import async_session_factory


def _day(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)


async def run(args: argparse.Namespace) -> int:
    until = _day(args.until) if args.until else datetime.now(UTC)
    since = _day(args.since) if args.since else until - timedelta(days=args.days)
    if since >= until:
        print("--since must be before --until", file=sys.stderr)
        return 2
    async with async_session_factory() as session:
        picks = await load_shown_picks(
            session, since=since, until=until, window_days=args.window_days
        )
    report = summarize(
        picks, since=since, until=until, window_days=args.window_days, min_group=args.min_group
    )
    print(format_report(report))
    if args.json:
        args.json.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        print(f"wrote {args.json}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Engagement with recommendations")
    parser.add_argument("--days", type=int, default=30, help="Period length when --since is not set")
    parser.add_argument("--since", help="Start date, YYYY-MM-DD (UTC)")
    parser.add_argument("--until", help="End date, YYYY-MM-DD (UTC, exclusive); default now")
    parser.add_argument(
        "--window-days", type=int, default=7, help="How long after a pick an action still counts"
    )
    parser.add_argument("--min-group", type=int, default=DEFAULT_MIN_GROUP)
    parser.add_argument("--json", type=Path, help="Also write the raw numbers here")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
