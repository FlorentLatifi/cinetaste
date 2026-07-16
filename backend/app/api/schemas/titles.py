from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GenreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class ReasonOut(BaseModel):
    code: str
    message: str
    evidence: dict = Field(default_factory=dict)


class TitleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    media_type: str
    name: str
    overview: str | None
    release_date: date | None
    runtime: int | None
    popularity: float
    vote_average: float
    poster_path: str | None
    backdrop_path: str | None
    original_language: str | None
    genres: list[GenreOut] = Field(default_factory=list)

    @property
    def poster_url(self) -> str | None:
        if not self.poster_path:
            return None
        if self.poster_path.startswith("http"):
            return self.poster_path
        return f"https://image.tmdb.org/t/p/w500{self.poster_path}"


class TitleSummaryOut(TitleSummary):
    poster_url: str | None = None

    @classmethod
    def from_title(cls, title) -> TitleSummaryOut:
        data = TitleSummary.model_validate(title)
        poster = None
        if title.poster_path:
            poster = (
                title.poster_path
                if title.poster_path.startswith("http")
                else f"https://image.tmdb.org/t/p/w500{title.poster_path}"
            )
        return cls(**data.model_dump(), poster_url=poster)


class RecommendationItemOut(BaseModel):
    title: TitleSummaryOut
    score: float
    reasons: list[ReasonOut]


class RecommendationSlateOut(BaseModel):
    items: list[RecommendationItemOut]


class InteractionRequest(BaseModel):
    event_type: str = Field(
        pattern=(
            "^(like|dislike|watchlist|not_interested|skip|view|"
            "haven't_seen|rate_1|rate_2|rate_3|rate_4)$"
        )
    )


class OnboardingReaction(BaseModel):
    """One card decision during onboarding.

    Actions:
    - haven't_seen — zero taste signal
    - not_interested — mild negative signal
    - rate_1 … rate_4 — Bad / It's ok / Good / Favorite
    - like / dislike — legacy aliases (mapped to rate_3 / rate_1)
    """

    title_id: UUID
    action: str = Field(
        pattern=(
            "^(haven't_seen|not_interested|rate_1|rate_2|rate_3|rate_4|like|dislike)$"
        )
    )


class OnboardingCompleteRequest(BaseModel):
    reactions: list[OnboardingReaction] = Field(min_length=1, max_length=80)


class OnboardingCardsOut(BaseModel):
    items: list[TitleSummaryOut]


class CatalogStatusOut(BaseModel):
    title_count: int
    with_embeddings: int
    ready_for_onboarding: bool
