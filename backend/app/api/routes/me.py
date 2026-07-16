from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, get_auth_service, get_settings_dep
from app.api.schemas.auth import DeleteAccountRequest, UserResponse
from app.application.auth_service import AuthService
from app.core.config import Settings
from app.core.cookies import clear_refresh_cookie
from app.domain.exceptions import AppError

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    body: DeleteAccountRequest,
    user: CurrentUser,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    """Permanently delete the authenticated account and cascaded taste data."""
    if body.confirm.strip().upper() != "DELETE":
        raise AppError(
            'Type DELETE to confirm account deletion',
            status_code=400,
            code="delete_not_confirmed",
        )
    await auth.delete_account(user=user, password=body.password)
    clear_refresh_cookie(response, settings)
