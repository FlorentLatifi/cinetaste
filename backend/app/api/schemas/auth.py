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


class TokenResponse(BaseModel):
    """Access JWT + user. Refresh token is httpOnly cookie only (not in body)."""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse
