"""Rebuilding the taste profile after the response instead of during it.

The rebuild re-reads the whole interaction history: 8.8 ms at ten ratings,
324 ms at two thousand, on every single rating. Deferring it is not just
``recompute=False`` — the For You cache is keyed by ``profile.version``, so a
rating that skips the bump produces the identical slate and the screen does not
change. These tests pin the split: bump now, rebuild behind.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.application.taste_recompute import recompute_profile_after_response
from app.application.taste_service import TasteService
from app.core.config import Settings
from app.infrastructure.db.models.taste import TasteProfile


def _settings(**overrides) -> Settings:
    data = {
        "jwt_secret": "unit-test-secret-key-at-least-32-chars!",
        "database_url": "postgresql+asyncpg://u:p@localhost/x",
    }
    data.update(overrides)
    return Settings(**data)


# ------------------------------------------------------------- version bump


@pytest.mark.asyncio
async def test_first_rating_creates_a_profile_to_version() -> None:
    """A user rating for the first time has no profile row yet."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()

    version = await TasteService(session).bump_profile_version(uuid4())

    assert version == 1
    created = session.add.call_args[0][0]
    assert isinstance(created, TasteProfile)
    assert created.features == {} and created.vector is None


@pytest.mark.asyncio
async def test_bump_increments_without_touching_the_model() -> None:
    """The expensive part — features and vector — is left alone on purpose."""
    profile = TasteProfile(
        user_id=uuid4(), version=7, features={"genre:drama": 1.2}, vector=[0.1] * 384
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=profile)
    session.add = MagicMock()
    session.flush = AsyncMock()

    version = await TasteService(session).bump_profile_version(profile.user_id)

    assert version == 8
    assert profile.features == {"genre:drama": 1.2}
    assert profile.vector is not None
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_a_null_version_is_treated_as_one() -> None:
    profile = TasteProfile(user_id=uuid4(), version=None, features={}, vector=None)
    session = AsyncMock()
    session.get = AsyncMock(return_value=profile)
    session.flush = AsyncMock()

    assert await TasteService(session).bump_profile_version(profile.user_id) == 2


# ------------------------------------------------------- the background task


@pytest.mark.asyncio
async def test_the_rebuild_drops_the_slate_it_superseded() -> None:
    """recompute bumps the version again, so the interim slate is stale."""
    user_id = uuid4()
    store = AsyncMock()
    rebuilt = TasteProfile(user_id=user_id, version=9, features={}, vector=None)

    with (
        patch("app.application.taste_recompute.async_session_factory") as factory,
        patch("app.application.taste_recompute.get_store", return_value=store),
    ):
        session = AsyncMock()
        session.commit = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch.object(
            TasteService, "recompute_profile", AsyncMock(return_value=rebuilt)
        ) as recompute:
            await recompute_profile_after_response(user_id, _settings())

    recompute.assert_awaited_once_with(user_id)
    session.commit.assert_awaited_once()
    store.drop_tracked.assert_awaited_once_with(f"slateidx:{user_id}")


@pytest.mark.asyncio
async def test_a_failed_rebuild_is_logged_not_raised(caplog) -> None:
    """The response has already gone out; raising here reaches nobody.

    The rating itself is committed, so the cost of this failure is a profile
    that is one interaction stale until the next rating rebuilds it.
    """
    import logging

    with (
        patch("app.application.taste_recompute.async_session_factory") as factory,
        caplog.at_level(logging.ERROR, logger="app.application.taste_recompute"),
    ):
        factory.side_effect = RuntimeError("database went away")
        await recompute_profile_after_response(uuid4(), _settings())

    assert "taste_recompute_deferred_failed" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_cache_drop_is_also_survivable(caplog) -> None:
    import logging

    store = AsyncMock()
    store.drop_tracked = AsyncMock(side_effect=ConnectionError("redis is down"))
    rebuilt = TasteProfile(user_id=uuid4(), version=2, features={}, vector=None)

    with (
        patch("app.application.taste_recompute.async_session_factory") as factory,
        patch("app.application.taste_recompute.get_store", return_value=store),
        patch.object(TasteService, "recompute_profile", AsyncMock(return_value=rebuilt)),
        caplog.at_level(logging.ERROR, logger="app.application.taste_recompute"),
    ):
        session = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        await recompute_profile_after_response(rebuilt.user_id, _settings())

    assert "taste_recompute_deferred_failed" in caplog.text


def test_deferral_is_on_by_default_and_can_be_turned_off() -> None:
    assert _settings().taste_recompute_deferred is True
    assert _settings(taste_recompute_deferred=False).taste_recompute_deferred is False
