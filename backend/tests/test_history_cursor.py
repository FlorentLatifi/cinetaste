from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.application.history_cursor import (
    CursorError,
    decode_history_cursor,
    encode_history_cursor,
)


def test_encode_decode_roundtrip() -> None:
    ts = datetime(2026, 3, 15, 12, 30, 0, tzinfo=timezone.utc)
    tid = uuid4()
    cursor = encode_history_cursor(ts, tid)
    out_ts, out_id = decode_history_cursor(cursor)
    assert out_id == tid
    assert out_ts == ts


def test_naive_datetime_assumed_utc() -> None:
    ts = datetime(2026, 1, 1, 0, 0, 0)  # naive
    tid = UUID("22222222-2222-4222-8222-222222222222")
    cursor = encode_history_cursor(ts, tid)
    out_ts, out_id = decode_history_cursor(cursor)
    assert out_id == tid
    assert out_ts.tzinfo is not None


def test_decode_rejects_garbage() -> None:
    with pytest.raises(CursorError):
        decode_history_cursor("not-a-cursor!!!")
    with pytest.raises(CursorError):
        decode_history_cursor("")
    with pytest.raises(CursorError):
        decode_history_cursor(encode_history_cursor(datetime.now(timezone.utc), uuid4())[:-4] + "xxxx")
