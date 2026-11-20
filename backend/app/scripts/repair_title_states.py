"""Recompute ``user_title_state`` from the interaction log.

Before ``resolve_title_state`` existed, ``record_interaction`` overwrote the
stored state with whatever the newest event implied, so rows can disagree with
the event log:

* ``rate_4 → watchlist`` stored ``watchlist`` (the rating vanished from History);
* ``rate_4 → watched`` stored ``watched`` ("Seen it · no rating");
* ``rate_3 → haven't_seen`` stored ``haven't_seen``, which is not excluded from
  For You, so a rated title could be recommended again.

This script derives each (user, title) state from that pair's events with the
same function the API now uses, and reports — or, with ``--apply``, fixes — the
rows that differ. It is idempotent: a second run finds nothing to change.

    python -m app.scripts.repair_title_states              # dry run: counts only
    python -m app.scripts.repair_title_states --verbose    # dry run + every change
    python -m app.scripts.repair_title_states --apply      # write the fixes

Guarantees:

* Reads events in one ordered stream and one user's stored rows at a time, so
  memory is bounded by the largest single user's history.
* Never touches the event log or taste profiles. ``updated_at`` is preserved so
  History keeps its order.
* Each update is compare-and-set on the old state: a row a live request changed
  while the script ran is skipped (and counted), not overwritten.
* Rows with no events at all are left alone and only counted.

Cached For You slates are keyed by profile version and expire after
``REC_CACHE_TTL_SECONDS``; a slate cached before the repair can show a title
that the repair now excludes until then.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.taste_signals import resolve_title_state
from app.infrastructure.db.models.interaction import InteractionEvent, UserTitleState

_STREAM_BATCH = 5_000


@dataclass(frozen=True, slots=True)
class StateChange:
    user_id: UUID
    title_id: UUID
    # None when the row does not exist yet.
    old: str | None
    new: str


def plan_user_changes(
    user_id: UUID,
    events_by_title: dict[UUID, list[str]],
    stored: dict[UUID, str],
) -> list[StateChange]:
    """Rows of one user whose stored state disagrees with their events.

    ``events_by_title`` lists each title's event types oldest first. A title
    with events but no row gets one only when the events imply a real state;
    the API used to create ``none`` rows for view/skip and those are harmless.
    """
    changes: list[StateChange] = []
    for title_id, event_types in events_by_title.items():
        new = resolve_title_state(event_types).state
        old = stored.get(title_id)
        if old == new or (old is None and new == "none"):
            continue
        changes.append(StateChange(user_id=user_id, title_id=title_id, old=old, new=new))
    return changes


def _describe_database() -> str:
    url = make_url(get_settings().database_url)
    return f"{url.host or 'localhost'}:{url.port or 5432}/{url.database}"


async def _stored_states(session: AsyncSession, user_id: UUID) -> dict[UUID, str]:
    rows = await session.execute(
        select(UserTitleState.title_id, UserTitleState.state).where(
            UserTitleState.user_id == user_id
        )
    )
    return {r.title_id: r.state for r in rows}


async def _apply(session: AsyncSession, changes: Iterable[StateChange]) -> tuple[int, int]:
    """Write changes; returns (applied, skipped because the row moved)."""
    applied = skipped = 0
    for c in changes:
        if c.old is None:
            session.add(UserTitleState(user_id=c.user_id, title_id=c.title_id, state=c.new))
            applied += 1
            continue
        result = await session.execute(
            update(UserTitleState)
            .where(
                UserTitleState.user_id == c.user_id,
                UserTitleState.title_id == c.title_id,
                UserTitleState.state == c.old,
            )
            # Assigning the column to itself keeps ``onupdate=now()`` from firing:
            # a repair is not a user action and must not reorder History.
            .values(state=c.new, updated_at=UserTitleState.updated_at)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            applied += 1
        else:
            skipped += 1
    await session.flush()
    return applied, skipped


async def run(session: AsyncSession, *, apply: bool, verbose: bool) -> dict[str, object]:
    stats: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    affected_users: set[UUID] = set()

    async def flush_user(user_id: UUID, events_by_title: dict[UUID, list[str]]) -> None:
        stored = await _stored_states(session, user_id)
        stats["users_scanned"] += 1
        stats["pairs_scanned"] += len(events_by_title)
        stats["rows_without_events"] += len(set(stored) - set(events_by_title))
        changes = plan_user_changes(user_id, events_by_title, stored)
        if not changes:
            return
        affected_users.add(user_id)
        for c in changes:
            transitions[f"{c.old or '<missing>'} -> {c.new}"] += 1
            if verbose:
                print(f"  user={c.user_id} title={c.title_id}: {c.old or '<missing>'} -> {c.new}")
        stats["rows_to_change"] += len(changes)
        if apply:
            applied, skipped = await _apply(session, changes)
            stats["rows_changed"] += applied
            stats["rows_skipped_concurrent_change"] += skipped

    stream = await session.stream(
        select(InteractionEvent.user_id, InteractionEvent.title_id, InteractionEvent.event_type)
        .order_by(InteractionEvent.user_id, InteractionEvent.title_id, InteractionEvent.created_at)
        .execution_options(yield_per=_STREAM_BATCH)
    )
    current_user: UUID | None = None
    events_by_title: dict[UUID, list[str]] = {}
    async for user_id, title_id, event_type in stream:
        if user_id != current_user:
            if current_user is not None:
                await flush_user(current_user, events_by_title)
            current_user, events_by_title = user_id, {}
        events_by_title.setdefault(title_id, []).append(event_type)
    if current_user is not None:
        await flush_user(current_user, events_by_title)

    return {
        **dict(stats),
        "users_affected": len(affected_users),
        "transitions": dict(transitions.most_common()),
        "mode": "apply" if apply else "dry-run",
    }


async def main(*, apply: bool, verbose: bool) -> int:
    # Imported here so ``plan_user_changes`` stays importable without a DB.
    from app.infrastructure.db.session import async_session_factory

    print(f"Database: {_describe_database()} ({'APPLY' if apply else 'dry run'})")
    async with async_session_factory() as session:
        stats = await run(session, apply=apply, verbose=verbose)
        if apply:
            await session.commit()
        else:
            await session.rollback()
    print(stats)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recompute user_title_state from interaction events (dry run by default)"
    )
    parser.add_argument("--apply", action="store_true", help="Write the fixes (default: dry run)")
    parser.add_argument("--verbose", action="store_true", help="Print every row that would change")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply, verbose=args.verbose)))
