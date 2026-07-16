from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """Optional body; browser clients should rely on the httpOnly cookie."""

    refresh_token: str | None = Field(default=None, min_length=10)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=10)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str | None
    onboarding_completed_at: datetime | None
    created_at: datetime


class TasteFeatureOut(BaseModel):
    key: str
    family: str
    label: str
    weight: float


class TasteSummaryOut(BaseModel):
    """Interpretable slice of the user's taste profile for the Account page."""

    version: int
    updated_at: datetime | None = None
    has_vector: bool = False
    feature_count: int = 0
    anchor_count: int = 0
    has_import_overlay: bool = False
    import_overlay_count: int = 0
    likes: list[TasteFeatureOut] = Field(default_factory=list)
    dislikes: list[TasteFeatureOut] = Field(default_factory=list)
    ready: bool = False


class TasteAnchorOut(BaseModel):
    name: str
    year: int | None = None


class TasteExportOut(BaseModel):
    """Downloadable taste snapshot (no dense embedding)."""

    schema_version: str = Field(
        default="cinetaste.taste_snapshot.v1",
        alias="schema",
        serialization_alias="schema",
    )
    exported_at: str
    profile_version: int = 0
    updated_at: str | None = None
    has_vector: bool = False
    feature_count: int = 0
    anchor_count: int = 0
    likes: list[TasteFeatureOut] = Field(default_factory=list)
    dislikes: list[TasteFeatureOut] = Field(default_factory=list)
    anchors: list[TasteAnchorOut] = Field(default_factory=list)
    text: str = Field(description="Plain-text share format")

    model_config = ConfigDict(populate_by_name=True)


class TasteImportRequest(BaseModel):
    """Merge a previously exported taste snapshot into the live profile."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: str = Field(
        default="cinetaste.taste_snapshot.v1",
        alias="schema",
        description='Must be "cinetaste.taste_snapshot.v1"',
    )
    likes: list[TasteFeatureOut] = Field(default_factory=list, max_length=40)
    dislikes: list[TasteFeatureOut] = Field(default_factory=list, max_length=40)


class TasteImportResultOut(BaseModel):
    """Result of a snapshot merge."""

    merged_features: int
    profile_version: int
    summary: TasteSummaryOut


class TokenResponse(BaseModel):
    """Access JWT + user. Refresh token is httpOnly cookie only (not in body)."""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Always the same message — does not reveal whether the email exists.

    ``dev_reset_token`` is only populated outside production for local testing
    (no email provider in MVP).
    """

    message: str = (
        "If an account exists for that email, password reset instructions have been issued."
    )
    dev_reset_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirm: str = Field(
        description='Must be the literal string "DELETE"',
        min_length=6,
        max_length=16,
    )
