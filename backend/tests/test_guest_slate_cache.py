"""Guest slate cache: what makes two requests "the same", and that a bad cache is only a miss."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.application.guest_slates import GuestSlateCache, guest_slate_cache_key
from app.application.onboarding_service import normalize_reactions
from app.infrastructure.cache import MemoryStore

A, B, C = (UUID(int=i) for i in (1, 2, 3))


def _key(reactions: list[dict[str, str]], limit: int = 12) -> str:
    return guest_slate_cache_key(normalize_reactions(reactions), limit)


def _rx(title_id: UUID, action: str) -> dict[str, str]:
    return {"title_id": str(title_id), "action": action}


def test_same_answers_same_key() -> None:
    answers = [_rx(A, "rate_4"), _rx(B, "rate_1"), _rx(C, "haven't_seen")]
    assert _key(answers) == _key(list(answers))


def test_key_shape_is_fixed_length_and_hides_ids() -> None:
    key = _key([_rx(A, "rate_4")])
    assert key.startswith("guest:slate:")
    assert str(A) not in key
    many = _key([_rx(uuid4(), "rate_3") for _ in range(80)])
    assert len(many) == len(key)


def test_a_changed_answer_supersedes_the_first_like_ranking_does() -> None:
    # normalize_reactions: the last answer for a card wins, so these are one input.
    changed = [_rx(A, "rate_1"), _rx(B, "rate_4"), _rx(A, "rate_4")]
    final = [_rx(B, "rate_4"), _rx(A, "rate_4")]
    assert _key(changed) == _key(final)


def test_legacy_spellings_and_unknown_actions_normalize_away() -> None:
    assert _key([_rx(A, "like"), _rx(B, "rate_4")]) == _key([_rx(A, "rate_3"), _rx(B, "rate_4")])
    assert _key([_rx(A, "dislike"), _rx(B, "rate_4")]) == _key([_rx(A, "rate_1"), _rx(B, "rate_4")])
    # An action we don't know carries no signal, so it can't make a different slate.
    assert _key([_rx(A, "rate_4"), _rx(B, "frobnicate")]) == _key([_rx(A, "rate_4")])


def test_uuid_spelling_does_not_matter() -> None:
    upper = [{"title_id": str(A).upper(), "action": "rate_4"}]
    assert _key(upper) == _key([_rx(A, "rate_4")])


def test_different_rating_different_title_or_limit_gives_a_different_key() -> None:
    base = [_rx(A, "rate_4"), _rx(B, "rate_2")]
    assert _key(base) != _key([_rx(A, "rate_3"), _rx(B, "rate_2")])
    assert _key(base) != _key([_rx(A, "rate_4"), _rx(C, "rate_2")])
    assert _key(base, limit=12) != _key(base, limit=13)
    assert _key(base) != _key(base[:1])


def test_answer_order_is_part_of_the_key() -> None:
    """Order is the event order the profile builder sees; it is not assumed away."""
    forward = [_rx(A, "rate_4"), _rx(B, "rate_2")]
    assert _key(forward) != _key(list(reversed(forward)))


ITEMS = [{"title_id": str(A), "score": 0.5, "reasons": [{"code": "x", "message": "because"}]}]


async def test_roundtrip_through_the_store() -> None:
    cache = GuestSlateCache(MemoryStore(), ttl_seconds=60)
    assert await cache.get("k") is None
    await cache.set("k", ITEMS)
    assert await cache.get("k") == ITEMS


async def test_ttl_zero_disables_the_cache() -> None:
    store = MemoryStore()
    cache = GuestSlateCache(store, ttl_seconds=0)
    await cache.set("k", ITEMS)
    assert await store.get("k") is None
    assert await cache.get("k") is None


async def test_an_empty_slate_is_not_pinned() -> None:
    store = MemoryStore()
    await GuestSlateCache(store, ttl_seconds=60).set("k", [])
    assert await store.get("k") is None


@pytest.mark.parametrize("raw", ["not json", "{}", '{"items": []}', '{"items": "x"}', "null"])
async def test_a_corrupt_entry_is_a_miss_not_an_error(raw: str) -> None:
    store = MemoryStore()
    await store.set("k", raw, ttl_seconds=60)
    assert await GuestSlateCache(store, ttl_seconds=60).get("k") is None


async def test_a_failing_store_never_breaks_the_request() -> None:
    class Broken(MemoryStore):
        async def get(self, key: str) -> str | None:
            raise ConnectionError("redis down")

        async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
            raise ConnectionError("redis down")

    cache = GuestSlateCache(Broken(), ttl_seconds=60)
    assert await cache.get("k") is None
    await cache.set("k", ITEMS)  # must not raise


def test_payload_is_compact_json() -> None:
    from app.application.recommendation_service import RecommendationService
    from app.recommendation.explanations import Reason
    from app.recommendation.pipeline import RankedItem

    item = RankedItem(
        title_id=A, score=0.25, reasons=[Reason(code="c", message="m", evidence={"big": "x" * 500})]
    )
    payload = RecommendationService.slate_payload([item])
    assert payload == [{"title_id": str(A), "score": 0.25, "reasons": [{"code": "c", "message": "m"}]}]
    assert "big" not in json.dumps(payload), "evidence is not stored"
