"""The impression → outcome join, against real Postgres."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.engagement import load_shown_picks, summarize
from app.infrastructure.db.models.interaction import InteractionEvent, RecommendationImpression
from app.infrastructure.db.models.user import User
from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 2, 12, tzinfo=UTC)


async def test_each_pick_gets_its_first_answer_within_the_window(db_session: AsyncSession) -> None:
    titles = await seed_catalog(db_session, count=8)
    me = User(email=f"eng-{uuid4().hex[:8]}@example.com", password_hash="x")
    other = User(email=f"eng-{uuid4().hex[:8]}@example.com", password_hash="x")
    db_session.add_all([me, other])
    await db_session.flush()

    def shown(user, title, position, at, codes=("because_you_liked",)):
        db_session.add(
            RecommendationImpression(
                user_id=user.id, title_id=title.id, slate_id=uuid4(), position=position,
                score=0.5, reason_codes=list(codes), created_at=at,
            )
        )

    def did(user, title, event, at):
        db_session.add(
            InteractionEvent(user_id=user.id, title_id=title.id, event_type=event, weight=0.0, created_at=at)
        )

    saved, loved, passed, ignored, late, before, cleared, theirs = titles
    shown(me, saved, 0, T0, ("because_you_liked", "hidden_gem"))
    shown(me, saved, 4, T0 + timedelta(days=1))  # shown again: same pick, first position kept
    did(me, saved, "watchlist", T0 + timedelta(hours=1))

    shown(me, loved, 1, T0)
    did(me, loved, "rate_4", T0 + timedelta(minutes=5))
    did(me, loved, "not_interested", T0 + timedelta(minutes=9))  # later answer: first one counts

    shown(me, passed, 2, T0, ("discovery",))
    did(me, passed, "not_interested", T0 + timedelta(days=2))

    shown(me, ignored, 3, T0)
    did(me, ignored, "view", T0 + timedelta(minutes=1))  # not an answer

    shown(me, late, 5, T0)
    did(me, late, "watchlist", T0 + timedelta(days=8))  # outside the 7-day window

    shown(me, before, 6, T0)
    did(me, before, "rate_4", T0 - timedelta(days=1))  # before it was shown

    shown(me, cleared, 7, T0)
    did(me, cleared, "clear", T0 + timedelta(minutes=1))
    did(me, cleared, "watchlist", T0 + timedelta(minutes=2))

    shown(me, theirs, 8, T0)
    did(other, theirs, "watchlist", T0 + timedelta(minutes=1))  # someone else's action

    shown(me, titles[0], 0, T0 - timedelta(days=30))  # outside the period entirely
    await db_session.commit()

    since, until = T0 - timedelta(days=1), T0 + timedelta(days=10)
    picks = await load_shown_picks(db_session, since=since, until=until, window_days=7)

    by_position = {p.position: p for p in picks}
    assert len(picks) == 8
    assert by_position[0].outcome == "saved"
    assert set(by_position[0].reason_codes) == {"because_you_liked", "hidden_gem"}
    assert by_position[1].outcome == "seen_liked"
    assert by_position[2].outcome == "passed"
    assert by_position[3].outcome is None
    assert by_position[5].outcome is None
    assert by_position[6].outcome is None
    assert by_position[7].outcome == "saved"
    assert by_position[8].outcome is None

    report = summarize(picks, since=since, until=until, window_days=7, min_group=1)
    assert report.overall.rates(1)["saved"] == pytest.approx(2 / 8)
    assert report.by_reason["discovery"].rates(1)["passed"] == 1.0
