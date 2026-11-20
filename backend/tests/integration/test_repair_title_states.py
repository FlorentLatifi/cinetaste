"""repair_title_states against a real database: dry run, apply, idempotent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.taste_signals import weight_for
from app.infrastructure.db.models.interaction import InteractionEvent, UserTitleState
from app.infrastructure.db.models.user import User
from app.scripts.repair_title_states import run
from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration


async def test_repair_recomputes_states_written_under_the_old_rule(
    db_session: AsyncSession,
) -> None:
    titles = await seed_catalog(db_session, count=6)
    user = User(id=uuid4(), email=f"repair-{uuid4().hex[:8]}@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    # Plain ids: the rollback after the dry run expires ORM instances.
    user_id = user.id
    title_ids = [t.id for t in titles]

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    # (events oldest first, state the old code stored, expected after repair)
    cases = [
        (["rate_4", "watchlist"], "watchlist", "like"),
        (["rate_4", "watched"], "watched", "like"),
        (["rate_3", "haven't_seen"], "haven't_seen", "like"),
        (["watchlist", "rate_3"], "like", "like"),
    ]
    old_updated_at = t0 - timedelta(days=3)
    for title_id, (events, old_state, _new) in zip(title_ids, cases, strict=False):
        for i, event in enumerate(events):
            db_session.add(
                InteractionEvent(
                    user_id=user_id,
                    title_id=title_id,
                    event_type=event,
                    weight=weight_for(event),
                    created_at=t0 + timedelta(minutes=i),
                )
            )
        db_session.add(
            UserTitleState(
                user_id=user_id, title_id=title_id, state=old_state, updated_at=old_updated_at
            )
        )
    await db_session.commit()

    async def stored() -> dict:
        rows = await db_session.execute(
            select(UserTitleState.title_id, UserTitleState.state, UserTitleState.updated_at).where(
                UserTitleState.user_id == user_id
            )
        )
        return {r.title_id: (r.state, r.updated_at) for r in rows}

    dry = await run(db_session, apply=False, verbose=False)
    await db_session.rollback()
    assert dry["rows_to_change"] == 3
    assert "rows_changed" not in dry
    assert {s for s, _ in (await stored()).values()} == {
        "watchlist",
        "watched",
        "haven't_seen",
        "like",
    }

    applied = await run(db_session, apply=True, verbose=False)
    await db_session.commit()
    assert applied["rows_changed"] == 3
    after = await stored()
    for title_id, (_events, _old, new) in zip(title_ids, cases, strict=False):
        state, updated_at = after[title_id]
        assert state == new
        # A repair is not a user action: History order is preserved.
        assert updated_at == old_updated_at

    again = await run(db_session, apply=True, verbose=False)
    await db_session.commit()
    assert again.get("rows_to_change", 0) == 0
