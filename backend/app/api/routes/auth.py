from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, get_auth_service, get_settings_dep
from app.api.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationResponse,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
)
from app.application.auth_service import AuthService
from app.core.config import Settings
from app.core.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.domain.exceptions import UnauthorizedError

router = APIRouter()


def _token_response(user, access: str, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        user=UserResponse.model_validate(user),
        email_verification_required=(
            settings.require_email_verification and user.email_verified_at is None
        ),
    )


def _refresh_from_request(request: Request, body: RefreshRequest | LogoutRequest | None) -> str:
    if body is not None and getattr(body, "refresh_token", None):
        return str(body.refresh_token)
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie:
        return cookie
    raise UnauthorizedError("Missing refresh token", code="invalid_refresh")


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TokenResponse:
    user, access, refresh = await auth.register(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
    )
    set_refresh_cookie(response, refresh, settings)
    return _token_response(user, access, settings)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TokenResponse:
    user, access, refresh = await auth.login(email=body.email, password=body.password)
    set_refresh_cookie(response, refresh, settings)
    return _token_response(user, access, settings)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: RefreshRequest | None = None,
) -> TokenResponse:
    raw = _refresh_from_request(request, body)
    user, access, new_refresh = await auth.refresh(refresh_token=raw)
    set_refresh_cookie(response, new_refresh, settings)
    return _token_response(user, access, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: LogoutRequest | None = None,
) -> None:
    try:
        raw = _refresh_from_request(request, body)
    except UnauthorizedError:
        clear_refresh_cookie(response, settings)
        return
    await auth.logout(refresh_token=raw)
    clear_refresh_cookie(response, settings)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> ForgotPasswordResponse:
    """Always 200 with a generic message (anti-enumeration)."""
    dev_token = await auth.request_password_reset(email=body.email)
    return ForgotPasswordResponse(dev_reset_token=dev_token)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    body: ResetPasswordRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    await auth.reset_password(token=body.token, new_password=body.new_password)


@router.post("/verify-email", response_model=UserResponse)
async def verify_email(
    body: VerifyEmailRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    """Consume a verification link. Unauthenticated: the link is the proof."""
    user = await auth.verify_email(token=body.token)
    return UserResponse.model_validate(user)


@router.post("/resend-verification", response_model=ResendVerificationResponse)
async def resend_verification(
    user: CurrentUser,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> ResendVerificationResponse:
    """Re-issue the link for the signed-in account.

    Authenticated on purpose: an open endpoint taking an email would mail
    anyone on request, and would confirm which addresses are registered.
    """
    dev_token = await auth.request_email_verification(user)
    return ResendVerificationResponse(dev_verification_token=dev_token)
