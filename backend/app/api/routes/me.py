from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_auth_service, get_settings_dep
from app.api.schemas.auth import DeleteAccountRequest, TasteFeatureOut, TasteSummaryOut, UserResponse
from app.api.schemas.titles import HistoryItemOut, TitleSummaryOut
from app.application.auth_service import AuthService
from app.application.recommendation_service import RecommendationService
from app.application.taste_service import TasteService
from app.application.taste_summary import summarize_profile_features
from app.core.config import Settings
from app.core.cookies import clear_refresh_cookie
from app.domain.exceptions import AppError
from app.domain.taste_signals import label_for_state
from app.infrastructure.db.session import get_db

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/me/taste", response_model=TasteSummaryOut)
async def my_taste(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TasteSummaryOut:
    """Top positive/negative taste features for the Account profile card."""
    taste = TasteService(session)
    profile = await taste.get_profile(user.id)
    if profile is None:
        return TasteSummaryOut(version=0, ready=False)

    summary = summarize_profile_features(profile.features, limit=8)
    has_vector = profile.vector is not None and len(list(profile.vector)) > 0
    ready = summary["feature_count"] > 0 or has_vector

    def map_chips(rows: list) -> list[TasteFeatureOut]:
        return [
            TasteFeatureOut(
                key=c.key,
                family=c.family,
                label=c.label,
                weight=c.weight,
            )
            for c in rows
        ]

    return TasteSummaryOut(
        version=int(profile.version or 1),
        updated_at=profile.updated_at,
        has_vector=has_vector,
        feature_count=summary["feature_count"],
        anchor_count=summary["anchor_count"],
        likes=map_chips(summary["likes"]),
        dislikes=map_chips(summary["dislikes"]),
        ready=ready,
    )


@router.get("/me/history", response_model=list[HistoryItemOut])
async def my_history(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(default=50, ge=1, le=100),
    state: str | None = Query(
        default=None,
        description=(
            "Optional filter: like | dislike | watchlist | not_interested | "
            "rated | watched"
        ),
    ),
) -> list[HistoryItemOut]:
    """Current likes, ratings, watchlist, passes — newest first."""
    from app.domain.taste_signals import HISTORY_VISIBLE_STATES

    if state is not None and state not in HISTORY_VISIBLE_STATES:
        raise AppError(
            f"Invalid history state filter: {state}",
            status_code=400,
            code="invalid_history_state",
        )

    service = RecommendationService(session, settings)
    rows = await service.history(user.id, limit=limit, state=state)
    return [
        HistoryItemOut(
            title=TitleSummaryOut.from_title(title),
            state=state_row.state,
            label=label_for_state(state_row.state),
            updated_at=state_row.updated_at,
        )
        for title, state_row in rows
    ]


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
