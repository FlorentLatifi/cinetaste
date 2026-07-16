from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import get_auth_service, get_settings_dep
from app.api.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
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


def _token_response(user, access: str) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        user=UserResponse.model_validate(user),
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
    return _token_response(user, access)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TokenResponse:
    user, access, refresh = await auth.login(email=body.email, password=body.password)
    set_refresh_cookie(response, refresh, settings)
    return _token_response(user, access)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: RefreshRequest = RefreshRequest(),
) -> TokenResponse:
    raw = _refresh_from_request(request, body)
    user, access, new_refresh = await auth.refresh(refresh_token=raw)
    set_refresh_cookie(response, new_refresh, settings)
    return _token_response(user, access)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: LogoutRequest = LogoutRequest(),
) -> None:
    try:
        raw = _refresh_from_request(request, body)
    except UnauthorizedError:
        clear_refresh_cookie(response, settings)
        return
    await auth.logout(refresh_token=raw)
    clear_refresh_cookie(response, settings)
