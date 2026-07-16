"""Onboarding completion gates and reaction handling (mocked deps, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.application.onboarding_service import (
    MIN_ONBOARDING_POSITIVE,
    MIN_ONBOARDING_RATINGS,
    OnboardingService,
)
from app.domain.exceptions import AppError


def _user() -> MagicMock:
    u = MagicMock()
    u.id = uuid4()
    u.onboarding_completed_at = None
    return u


def _service() -> tuple[OnboardingService, AsyncMock, AsyncMock, AsyncMock]:
    session = AsyncMock()
    session.flush = AsyncMock()
    taste = AsyncMock()
    taste.record_interaction = AsyncMock()
    rec = AsyncMock()
    rec.invalidate_user = AsyncMock()
    rec.onboarding_cards = AsyncMock(return_value=[MagicMock() for _ in range(10)])
    return OnboardingService(session, taste, rec), session, taste, rec


def _reactions(actions: list[str]) -> list[dict[str, str]]:
    return [{"title_id": str(uuid4()), "action": a} for a in actions]


@pytest.mark.asyncio
async def test_complete_rejects_empty_reactions() -> None:
    service, _, taste, _ = _service()
    with pytest.raises(AppError) as exc:
        await service.complete(_user(), [])
    assert exc.value.code == "onboarding_empty"
    taste.record_interaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_rejects_only_havent_seen() -> None:
    service, _, taste, _ = _service()
    actions = ["haven't_seen"] * 10
    with pytest.raises(AppError) as exc:
        await service.complete(_user(), _reactions(actions))
    assert exc.value.code == "onboarding_insufficient_ratings"
    # Interactions are still recorded (state/analytics) before the gate
    assert taste.record_interaction.await_count == 10


@pytest.mark.asyncio
async def test_complete_rejects_insufficient_positive() -> None:
    service, _, _, _ = _service()
    # 6 real ratings but all Bad
    actions = ["rate_1"] * MIN_ONBOARDING_RATINGS
    with pytest.raises(AppError) as exc:
        await service.complete(_user(), _reactions(actions))
    assert exc.value.code == "onboarding_insufficient_positive"


@pytest.mark.asyncio
async def test_complete_accepts_six_ratings_with_positives() -> None:
    service, session, taste, rec = _service()
    user = _user()
    actions = (
        ["rate_4", "rate_3"]
        + ["rate_2"] * 2
        + ["rate_1"] * 2
        + ["haven't_seen"] * 3
        + ["not_interested"]
    )
    assert sum(1 for a in actions if a.startswith("rate_")) >= MIN_ONBOARDING_RATINGS
    result = await service.complete(user, _reactions(actions))
    assert result is user
    assert user.onboarding_completed_at is not None
    assert user.onboarding_completed_at.tzinfo is not None
    assert taste.record_interaction.await_count == len(actions)
    session.flush.assert_awaited()
    rec.invalidate_user.assert_awaited_once_with(user.id)


@pytest.mark.asyncio
async def test_complete_maps_legacy_like_dislike() -> None:
    service, _, taste, _ = _service()
    user = _user()
    # 4 likes + 2 dislikes = 6 ratings, 4 positive
    actions = ["like"] * 4 + ["dislike"] * 2
    await service.complete(user, _reactions(actions))
    event_types = [
        c.kwargs["event_type"] for c in taste.record_interaction.await_args_list
    ]
    assert event_types.count("rate_3") == 4
    assert event_types.count("rate_1") == 2


@pytest.mark.asyncio
async def test_havent_seen_does_not_count_toward_rating_gate() -> None:
    service, _, _, _ = _service()
    # 5 real ratings + many unseen — still insufficient
    actions = ["rate_3"] * 5 + ["haven't_seen"] * 20
    with pytest.raises(AppError) as exc:
        await service.complete(_user(), _reactions(actions))
    assert exc.value.code == "onboarding_insufficient_ratings"


@pytest.mark.asyncio
async def test_cards_raises_when_catalog_empty() -> None:
    service, _, _, rec = _service()
    rec.onboarding_cards = AsyncMock(return_value=[MagicMock() for _ in range(3)])
    with pytest.raises(AppError) as exc:
        await service.cards(limit=15)
    assert exc.value.code == "catalog_empty"
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_cards_allows_short_batch_when_excluding() -> None:
    service, _, _, rec = _service()
    rec.onboarding_cards = AsyncMock(return_value=[MagicMock() for _ in range(2)])
    cards = await service.cards(limit=15, exclude_ids=[uuid4()])
    assert len(cards) == 2


@pytest.mark.asyncio
async def test_complete_ignores_unknown_actions() -> None:
    service, _, taste, _ = _service()
    user = _user()
    good = _reactions(["rate_3"] * 4 + ["rate_4"] * 2)
    good.append({"title_id": str(uuid4()), "action": "telepathy"})
    await service.complete(user, good)
    assert taste.record_interaction.await_count == 6


@pytest.mark.asyncio
async def test_min_gates_match_product_constants() -> None:
    assert MIN_ONBOARDING_RATINGS == 6
    assert MIN_ONBOARDING_POSITIVE == 2
