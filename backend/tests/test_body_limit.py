"""Request bodies over the limit are refused before anything parses them."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.middleware import BodySizeLimitMiddleware

LIMIT = 1024


async def echo(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"received": len(body), "head": body[:8].decode(errors="replace")})


def _client() -> AsyncClient:
    app = Starlette(routes=[Route("/echo", echo, methods=["GET", "POST"])])
    wrapped = BodySizeLimitMiddleware(app, max_bytes=LIMIT)
    return AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://test")


@pytest.mark.asyncio
async def test_a_body_within_the_limit_reaches_the_app_intact() -> None:
    async with _client() as client:
        res = await client.post("/echo", content=b"x" * LIMIT)
    assert res.status_code == 200
    assert res.json()["received"] == LIMIT


@pytest.mark.asyncio
async def test_a_declared_oversized_body_is_refused_unread() -> None:
    async with _client() as client:
        res = await client.post("/echo", content=b"x" * (LIMIT + 1))
    assert res.status_code == 413
    assert res.json()["code"] == "payload_too_large"


@pytest.mark.asyncio
async def test_a_chunked_body_without_a_length_is_capped_too() -> None:
    async def chunks(total: int):
        sent = 0
        while sent < total:
            piece = min(300, total - sent)
            sent += piece
            yield b"y" * piece

    async with _client() as client:
        big = await client.post("/echo", content=chunks(LIMIT * 3))
        small = await client.post("/echo", content=chunks(LIMIT - 10))

    assert big.status_code == 413
    # Under the limit, the buffered chunks are replayed in full and in order.
    assert small.status_code == 200
    assert small.json() == {"received": LIMIT - 10, "head": "yyyyyyyy"}


@pytest.mark.asyncio
async def test_requests_without_a_body_are_untouched() -> None:
    async with _client() as client:
        res = await client.get("/echo")
    assert res.status_code == 200
    assert res.json()["received"] == 0
