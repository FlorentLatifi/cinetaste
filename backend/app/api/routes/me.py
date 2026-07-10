from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.schemas.auth import UserResponse

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
