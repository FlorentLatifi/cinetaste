"""Request bodies reject what they do not declare.

Pydantic drops unknown keys by default. Nothing here is mass-assigned, so that
was never exploitable — but a typo in a client sailed through as a 200 with no
effect, which is the kind of bug that gets debugged from the wrong end.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas.auth import (
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TasteImportRequest,
)
from app.api.schemas.titles import InteractionRequest, OnboardingCompleteRequest


def test_unknown_fields_are_rejected_on_credential_bodies() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="secret-pass-1", role="admin")
    with pytest.raises(ValidationError):
        LoginRequest(email="a@b.com", password="secret-pass-1", scope="*")
    with pytest.raises(ValidationError):
        DeleteAccountRequest(password="secret-pass-1", confirm="DELETE", force=True)


def test_a_misspelled_field_fails_loudly() -> None:
    """The bug this catches: the password silently stayed unchanged."""
    with pytest.raises(ValidationError) as exc:
        ResetPasswordRequest(token="t" * 12, new_pasword="brand-new-pass-1")
    assert "new_pasword" in str(exc.value)


def test_known_fields_still_validate() -> None:
    assert RegisterRequest(email="a@b.com", password="secret-pass-1").password
    assert InteractionRequest(event_type="rate_4").event_type == "rate_4"
    assert (
        len(
            OnboardingCompleteRequest(
                reactions=[{"title_id": "11111111-1111-4111-8111-111111111111", "action": "rate_3"}]
            ).reactions
        )
        == 1
    )


def test_taste_import_accepts_a_whole_exported_snapshot() -> None:
    """Deliberately not strict.

    The documented flow is to upload the file from GET /me/taste/export as-is,
    and that file carries exported_at, anchors and text as well. Forbidding
    extras here would break the round trip the Account page offers.
    """
    body = TasteImportRequest(
        schema="cinetaste.taste_snapshot.v1",
        exported_at="2026-09-22T10:00:00+00:00",
        profile_version=3,
        updated_at=None,
        has_vector=True,
        feature_count=2,
        anchor_count=1,
        likes=[{"key": "genre:drama", "family": "genre", "label": "Drama", "weight": 1.2}],
        dislikes=[],
        anchors=[{"name": "Some Film", "year": 2011}],
        text="Likes: Drama",
    )
    assert body.schema_version == "cinetaste.taste_snapshot.v1"
    assert len(body.likes) == 1
