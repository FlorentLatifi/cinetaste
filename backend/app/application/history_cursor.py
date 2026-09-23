"""Keyset cursor for history pagination (updated_at DESC, title_id DESC).

The cursor is signed. Nothing sensitive is *in* it and the query it feeds is
always filtered by the authenticated user, so forging one could never reach
another account's rows — but an unsigned cursor is still a client-writable
value that reaches a SQL comparison, and it teaches whoever reads this code
that "opaque" and "tamper-proof" are different things. Signing costs one HMAC
per page and turns a parsing surface into a rejected request.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

# Truncated to 16 bytes: this authenticates a page offset, not a credential,
# and a shorter tag keeps the cursor small enough to sit in a query string.
_SIGNATURE_BYTES = 16


class CursorError(ValueError):
    """Invalid, malformed or unsigned history cursor."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign(body: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return _b64(digest[:_SIGNATURE_BYTES])


def encode_history_cursor(updated_at: datetime, title_id: UUID, *, secret: str) -> str:
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    payload = f"{updated_at.isoformat()}|{title_id}"
    body = _b64(payload.encode("utf-8"))
    return f"{body}.{_sign(body, secret)}"


def decode_history_cursor(cursor: str, *, secret: str) -> tuple[datetime, UUID]:
    if not cursor or not cursor.strip():
        raise CursorError("empty cursor")

    body, _, signature = cursor.strip().partition(".")
    if not signature:
        raise CursorError("unsigned cursor")
    # compare_digest, not ==: a plain comparison leaks how much of the tag
    # matched through its timing.
    if not hmac.compare_digest(signature, _sign(body, secret)):
        raise CursorError("cursor signature mismatch")

    try:
        decoded = _unb64(body).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CursorError("invalid cursor encoding") from exc

    try:
        ts_part, id_part = decoded.rsplit("|", 1)
        title_id = UUID(id_part)
        updated_at = datetime.fromisoformat(ts_part)
    except (ValueError, TypeError) as exc:
        raise CursorError("invalid cursor payload") from exc

    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at, title_id
