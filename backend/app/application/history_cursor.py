"""Keyset cursor for history pagination (updated_at DESC, title_id DESC)."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from uuid import UUID


class CursorError(ValueError):
    """Invalid or malformed history cursor."""


def encode_history_cursor(updated_at: datetime, title_id: UUID) -> str:
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    payload = f"{updated_at.isoformat()}|{title_id}"
    raw = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def decode_history_cursor(cursor: str) -> tuple[datetime, UUID]:
    if not cursor or not cursor.strip():
        raise CursorError("empty cursor")
    padded = cursor.strip() + "=" * (-len(cursor.strip()) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise CursorError("invalid cursor encoding") from exc
    try:
        ts_part, id_part = decoded.rsplit("|", 1)
        title_id = UUID(id_part)
        updated_at = datetime.fromisoformat(ts_part)
    except (ValueError, TypeError) as exc:
        raise CursorError("invalid cursor payload") from exc
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at, title_id
