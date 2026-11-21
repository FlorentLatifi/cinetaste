"""engagement_report against real Postgres: attribution rules and aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.interaction import InteractionEvent, RecommendationImpression
from app.infrastructure.db.models.user import User
from app.scripts.engagement_report import collect, ensure_local_database, event_classes
from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration

T0 = datetime(2026, 12, 1, 12, tzinfo=UTC)


async def test_report_counts_first_impressions_and_explicit_actions(
    db_session: AsyncSession,
) -> None:
    titles = await seed_catalog(db_session, count=6)
    alice = User(id=uuid4(), email=f"eng-a-{uuid4().hex[:8]}@example.com", password_hash="x")
    bob = User(id=uuid4(), email=f"eng-b-{uuid4().hex[:8]}@example.com", password_hash="x")
    db_session.add_all([alice, bob])
    await db_session.flush()

    def shown(user: User, title_index: int, position: int, codes: list[str], at: datetime) -> None:
        db_session.add(
            RecommendationImpression(
                user_id=user.id,
                title_id=titles[title_index].id,
                slate_id=uuid4(),
                position=position,
                reason_codes=codes,
                created_at=at,
            )
        )

    def did(user: User, title_index: int, event_type: str, at: datetime) -> None:
        db_session.add(
            InteractionEvent(
                user_id=user.id,
                title_id=titles[title_index].id,
                event_type=event_type,
                weight=1.0,
                created_at=at,
            )
        )

    shown(alice, 0, 0, ["same_director", "hidden_gem"], T0)
    shown(alice, 1, 1, ["similar_genres"], T0)
    shown(alice, 2, 2, ["similar_genres"], T0)
    shown(alice, 3, 3, ["hidden_gem"], T0)
    shown(alice, 0, 5, ["similar_genres"], T0 + timedelta(hours=1))  # re-shown: not counted again
    shown(bob, 4, 0, ["same_director"], T0)
    shown(bob, 5, 0, ["same_director"], T0 - timedelta(days=40))  # outside the period

    did(alice, 0, "watchlist", T0 + timedelta(minutes=10))  # positive
    did(alice, 0, "rate_1", T0 + timedelta(days=1))  # later event: the first one counts
    did(alice, 1, "not_interested", T0 + timedelta(days=1))  # negative
    did(alice, 2, "view", T0 + timedelta(minutes=5))  # implicit: not an action
    did(alice, 3, "rate_4", T0 + timedelta(days=10))  # outside the 7-day window
    did(bob, 4, "rate_4", T0 - timedelta(days=1))  # before the impression
    await db_session.flush()

    report = await collect(
        db_session, since=T0 - timedelta(days=7), until=T0 + timedelta(days=30), window_days=7
    )

    assert report.volume == {"impressions": 6, "slates": 6, "users": 2}
    (overall,) = report.tables["overview"]
    assert (overall["shown"], overall["acted"], overall["positive"], overall["negative"]) == (
        5,
        2,
        1,
        1,
    )
    by_position = {row["bucket"]: row for row in report.tables["by_position"]}
    assert by_position[1]["shown"] == 2 and by_position[1]["positive"] == 1  # alice#0, bob#4
    assert by_position[2]["negative"] == 1
    assert 6 not in by_position  # the re-shown card keeps its first position

    primary = {row["bucket"]: row["shown"] for row in report.tables["by_primary_reason"]}
    assert primary == {"same_director": 2, "similar_genres": 2, "hidden_gem": 1}
    any_reason = {row["bucket"]: row["shown"] for row in report.tables["by_any_reason"]}
    assert any_reason["hidden_gem"] == 2
    events = {row["bucket"]: row["shown"] for row in report.tables["by_event"]}
    assert events == {"(no action)": 3, "watchlist": 1, "not_interested": 1}
    low, high = overall["acted_ci"]
    assert low < overall["acted_rate"] < high


def test_event_classes_follow_the_signal_policies() -> None:
    classes = event_classes()
    assert {"watchlist", "rate_4", "like"} <= set(classes["positive"])
    assert {"not_interested", "rate_1", "dislike"} <= set(classes["negative"])
    assert "view" not in classes["counted"] and "haven't_seen" not in classes["counted"]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@ep-cool-name.eu-central-1.aws.neon.tech/neondb",
        "postgresql+asyncpg://u:p@cinetaste-db.internal:5432/cinetaste",
    ],
)
def test_remote_databases_are_refused(url: str) -> None:
    with pytest.raises(SystemExit, match="local database only"):
        ensure_local_database(url)


def test_local_databases_are_allowed() -> None:
    ensure_local_database("postgresql+asyncpg://u:p@localhost:5432/cinetaste")
    ensure_local_database("postgresql+asyncpg://u:p@db:5432/cinetaste")
