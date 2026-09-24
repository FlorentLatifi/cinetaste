"""Rebuilding the taste profile after the response, not during it.

`recompute_profile` re-reads the whole interaction history and every title it
mentions. Measured against a 10,000-title catalogue: 8.8 ms at ten ratings,
203 ms at a thousand, 324 ms at two thousand — and it ran inside the request
that recorded the rating, so the people who use the product most waited
longest.

Deferring it is not simply passing ``recompute=False``. The For You cache is
keyed by ``profile.version``, so a rating that does not bump the version
produces the same slate as before: the user rates a film and the screen does
not change, which is worse than a slow response. The two jobs are separated
instead — bump the version now so the slate is rebuilt, rebuild the profile
just after.

What the user sees immediately does not come from the profile anyway. The
rated title disappears from the feed because of ``user_title_state``, undo
works off the event log, and history reads the same rows. Only the *ranking*
waits, and it waits a few hundred milliseconds.

**Failure mode, stated plainly:** if the process dies between the response and
the task, that recompute is lost. The profile is then one interaction stale
with a version that says otherwise, until the next rating triggers a fresh
recompute. Nothing is corrupted and nothing needs repairing by hand — the log
line below is how you would know it happened.
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.application.taste_service import TasteService
from app.core.config import Settings
from app.infrastructure.cache import get_store
from app.infrastructure.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def recompute_profile_after_response(user_id: UUID, settings: Settings) -> None:
    """Rebuild one user's taste profile in a session of its own.

    The request's session is already committed and closed by the time this
    runs, so it opens another. Never raises: a failed recompute must not turn
    a rating that was recorded successfully into an error the user sees, and
    by this point the response has been sent regardless.
    """
    try:
        async with async_session_factory() as session:
            taste = TasteService(session, half_life_days=settings.taste_half_life_days)
            profile = await taste.recompute_profile(user_id)
            await session.commit()
            version = profile.version

        # recompute_profile bumped the version again, so any slate built from
        # the interim profile is now keyed against a stale one. Drop it.
        await get_store().drop_tracked(f"slateidx:{user_id}")
        logger.info("taste_recompute_deferred_done user_id=%s version=%s", user_id, version)
    except Exception:  # noqa: BLE001 — the response is already sent
        logger.exception("taste_recompute_deferred_failed user_id=%s", user_id)
