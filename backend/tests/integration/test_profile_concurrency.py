"""Two ratings in quick succession must both reach the taste profile.

Ratings rebuild the profile after the response, so a fast swiper starts
overlapping rebuilds. Before the fix, the rebuild that read the event log
first could commit last and overwrite the newer profile: the most recent
rating vanished from recommendations until the user rated something else.
These run against real Postgres because the bug lives in transaction
interleaving, which a mocked session cannot reproduce.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.application.taste_service as taste_service_module
from app.application.taste_service import TasteService
from app.infrastructure.db.models.interaction import InteractionEvent
from app.infrastructure.db.models.taste import TasteProfile
from app.infrastructure.db.models.user import User
from app.infrastructure.db.session import async_session_factory
from tests.integration.conftest import seed_catalog

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession) -> User:
    user = User(email=f"race-{uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(user)
    await session.commit()
    return user


async def _rate(user_id, title_id, event_type: str = "rate_4", weight: float = 1.55) -> None:
    async with async_session_factory() as session:
        session.add(
            InteractionEvent(user_id=user_id, title_id=title_id, event_type=event_type, weight=weight)
        )
        await session.commit()


def _director_of(title) -> str:
    return next(k for k in title.extra["feature_snapshot"] if k.startswith("person:director:"))


async def test_first_bump_creates_the_profile_at_version_one(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    async with async_session_factory() as session:
        assert await TasteService(session).bump_profile_version(user.id) == 1
        assert await TasteService(session).bump_profile_version(user.id) == 2
        await session.commit()


async def test_bump_leaves_features_and_vector_alone(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    db_session.add(
        TasteProfile(user_id=user.id, version=7, features={"genre:drama": 1.2}, vector=[0.1] * 384)
    )
    await db_session.commit()

    async with async_session_factory() as session:
        assert await TasteService(session).bump_profile_version(user.id) == 8
        await session.commit()
    async with async_session_factory() as session:
        profile = await session.get(TasteProfile, user.id)
        assert profile.features == {"genre:drama": 1.2}
        assert profile.vector is not None


async def test_concurrent_bumps_both_count(db_session: AsyncSession) -> None:
    """Two ratings at once used to read the same version and write the same next one."""
    user = await _user(db_session)

    async def bump() -> int:
        async with async_session_factory() as session:
            version = await TasteService(session).bump_profile_version(user.id)
            await asyncio.sleep(0.05)  # hold the transaction open across the other bump
            await session.commit()
            return version

    versions = await asyncio.gather(bump(), bump())
    assert sorted(versions) == [1, 2]


async def test_overlapping_rebuilds_keep_the_latest_rating(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The older rebuild must not overwrite the one that saw the newer rating.

    Rebuild A reads the log (one rating) and is held just before it writes.
    Rating B lands, rebuild B starts. Without serialisation B finishes first and
    A then overwrites it with a profile that never saw B.
    """
    titles = await seed_catalog(db_session, count=12)
    first, second = titles[0], titles[1]
    assert _director_of(first) != _director_of(second)
    user = await _user(db_session)
    await _rate(user.id, first.id)

    a_has_read = asyncio.Event()
    release_a = asyncio.Event()
    real_load = taste_service_module.load_profile_titles
    calls = 0

    async def gated_load(session, title_ids):
        nonlocal calls
        calls += 1
        rows = await real_load(session, title_ids)
        if calls == 1:  # rebuild A only
            a_has_read.set()
            await release_a.wait()
        return rows

    monkeypatch.setattr(taste_service_module, "load_profile_titles", gated_load)

    async def rebuild() -> None:
        async with async_session_factory() as session:
            await TasteService(session, half_life_days=0).recompute_profile(user.id)
            await session.commit()

    rebuild_a = asyncio.create_task(rebuild())
    await asyncio.wait_for(a_has_read.wait(), timeout=10)

    await _rate(user.id, second.id)
    rebuild_b = asyncio.create_task(rebuild())
    await asyncio.sleep(0.3)
    # B must be waiting for A rather than racing ahead of it.
    assert not rebuild_b.done()

    release_a.set()
    await asyncio.wait_for(asyncio.gather(rebuild_a, rebuild_b), timeout=20)

    async with async_session_factory() as session:
        profile = await session.get(TasteProfile, user.id)
        assert _director_of(second) in profile.features, "the newest rating was lost"
        assert _director_of(first) in profile.features
        assert profile.version == 2
