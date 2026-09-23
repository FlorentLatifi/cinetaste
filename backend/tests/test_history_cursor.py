from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.history_cursor import (
    CursorError,
    decode_history_cursor,
    encode_history_cursor,
)

SECRET = "unit-test-secret-key-at-least-32-chars!"


def test_encode_decode_roundtrip() -> None:
    ts = datetime(2026, 3, 15, 12, 30, 0, tzinfo=UTC)
    tid = uuid4()
    cursor = encode_history_cursor(ts, tid, secret=SECRET)
    out_ts, out_id = decode_history_cursor(cursor, secret=SECRET)
    assert out_id == tid
    assert out_ts == ts


def test_naive_datetime_assumed_utc() -> None:
    ts = datetime(2026, 1, 1, 0, 0, 0)  # naive
    tid = UUID("22222222-2222-4222-8222-222222222222")
    cursor = encode_history_cursor(ts, tid, secret=SECRET)
    out_ts, out_id = decode_history_cursor(cursor, secret=SECRET)
    assert out_id == tid
    assert out_ts.tzinfo is not None


def test_decode_rejects_garbage() -> None:
    with pytest.raises(CursorError):
        decode_history_cursor("not-a-cursor!!!", secret=SECRET)
    with pytest.raises(CursorError):
        decode_history_cursor("", secret=SECRET)
    with pytest.raises(CursorError):
        decode_history_cursor(
            encode_history_cursor(datetime.now(UTC), uuid4(), secret=SECRET)[:-4] + "xxxx",
            secret=SECRET,
        )


def test_tampered_payload_is_rejected() -> None:
    """The cursor reaches a SQL comparison, so it must be ours or nothing.

    Forging one could never cross into another account — the query is always
    filtered by the authenticated user — but an unsigned cursor is still a
    client-writable value feeding a query, and rejecting it is one HMAC.
    """
    cursor = encode_history_cursor(datetime.now(UTC), uuid4(), secret=SECRET)
    body, _, signature = cursor.partition(".")
    forged = encode_history_cursor(datetime(1999, 1, 1, tzinfo=UTC), uuid4(), secret=SECRET)
    forged_body = forged.partition(".")[0]

    # Someone else's body with this signature, and vice versa.
    with pytest.raises(CursorError):
        decode_history_cursor(f"{forged_body}.{signature}", secret=SECRET)
    with pytest.raises(CursorError):
        decode_history_cursor(f"{body}.{forged.partition('.')[2]}", secret=SECRET)


def test_unsigned_cursor_is_rejected() -> None:
    """An old-format cursor (bare base64) no longer decodes."""
    cursor = encode_history_cursor(datetime.now(UTC), uuid4(), secret=SECRET)
    with pytest.raises(CursorError):
        decode_history_cursor(cursor.partition(".")[0], secret=SECRET)


def test_cursor_from_another_secret_is_rejected() -> None:
    cursor = encode_history_cursor(datetime.now(UTC), uuid4(), secret=SECRET)
    with pytest.raises(CursorError):
        decode_history_cursor(cursor, secret="a-completely-different-secret-value-xx")
