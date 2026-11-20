"""Cache for guest slates: same answers in, same slate out, without ranking again.

A guest slate is a pure function of (the answers, their order, the limit) and the
catalog. Nothing about the person is part of it, so one stored copy can serve anyone
who sends the same answers — a reload, a double-fired request, or the SPA replaying
the cards after sign-up.

Key rules (``guest_slate_cache_key``):

* It is built from ``normalize_reactions`` output, so the things that cannot change the
  result cannot change the key: a card answered twice counts once (last answer wins),
  unknown actions are dropped, and the legacy ``like``/``dislike`` spellings map to the
  ratings they have always meant.
* **Order is kept.** Answer order is the event order the profile builder sees, so two
  orders are not provably the same input. A cache that must never serve a different
  answer than ranking would give does not get to guess.
* The limit is part of the key; a slate of 12 is not a slate of 30 cut short.
* Hashed, so the key is fixed-length however many answers there are, and carries no
  readable title ids into a shared store.
* ``_VERSION`` is bumped when ranking or the payload shape changes, so a deploy never
  serves a slate the new code would not have produced (Redis outlives deploys).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from app.infrastructure.cache import KeyValueStore

logger = logging.getLogger(__name__)

_VERSION = "v1"


def guest_slate_cache_key(reactions: Sequence[tuple[UUID, str]], limit: int) -> str:
    canonical = f"{limit}|" + ";".join(f"{title_id}:{event}" for title_id, event in reactions)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"guest:slate:{_VERSION}:{digest}"


class GuestSlateCache:
    def __init__(self, store: KeyValueStore, *, ttl_seconds: int) -> None:
        self._store = store
        self._ttl = ttl_seconds

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    async def get(self, key: str) -> list[dict[str, Any]] | None:
        """The stored items, or None. A broken cache is a miss, never an error."""
        if not self.enabled:
            return None
        try:
            raw = await self._store.get(key)
            if not raw:
                return None
            items = json.loads(raw)["items"]
            return items if isinstance(items, list) and items else None
        except Exception:  # noqa: BLE001
            logger.warning("guest_slate_cache_read_failed key=%s", key, exc_info=True)
            return None

    async def set(self, key: str, items: list[dict[str, Any]]) -> None:
        # An empty slate usually means the catalog is still filling; do not pin it.
        if not self.enabled or not items:
            return
        try:
            await self._store.set(key, json.dumps({"items": items}), ttl_seconds=self._ttl)
        except Exception:  # noqa: BLE001
            logger.warning("guest_slate_cache_write_failed key=%s", key, exc_info=True)
