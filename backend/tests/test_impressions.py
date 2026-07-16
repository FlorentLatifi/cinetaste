"""Unit tests for For You impression logging (no DB)."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.application.recommendation_service import RecommendationService
from app.recommendation.explanations import Reason
from app.recommendation.pipeline import RankedItem


def test_log_impressions_disabled_or_empty_returns_none() -> None:
    settings = SimpleNamespace(rec_log_impressions=False)
    svc = RecommendationService(session=SimpleNamespace(), settings=settings)  # type: ignore[arg-type]

    async def _run() -> None:
        assert await svc.log_impressions(uuid4(), []) is None
        settings.rec_log_impressions = True
        assert await svc.log_impressions(uuid4(), []) is None

    asyncio.run(_run())


def test_ranked_item_reason_codes_extractable() -> None:
    tid = uuid4()
    item = RankedItem(
        title_id=tid,
        score=0.9,
        reasons=[
            Reason(code="shared_genre", message="x", evidence={}),
            Reason(code="hidden_gem", message="y", evidence={}),
        ],
    )
    codes = [r.code for r in item.reasons][:8]
    assert codes == ["shared_genre", "hidden_gem"]
