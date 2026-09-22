"""A failed commit must never reach the client as 200 OK.

get_db commits when the dependency exits. From FastAPI 0.118 the default
(request-scoped) exit runs *after* the response is sent, so a commit failure
would be invisible to the client — a silently lost write. DbSession therefore
declares scope="function". This test fails if that is ever dropped.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import DbSession
from app.infrastructure.db.session import get_db


class _Session:
    async def commit(self) -> None:
        raise RuntimeError("commit failed")


async def _failing_commit_session():
    session = _Session()
    yield session
    await session.commit()


@pytest.mark.asyncio
async def test_commit_failure_is_reported_to_the_client() -> None:
    app = FastAPI()

    @app.post("/write")
    async def write(session: DbSession) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_db] = _failing_commit_session
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/write")
    assert response.status_code == 500


def test_every_route_uses_the_single_session_alias() -> None:
    """Two declarations of get_db with different scopes = two sessions per request."""
    import pathlib

    offenders = [
        str(path)
        for path in pathlib.Path("app").rglob("*.py")
        if "Depends(get_db)" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
