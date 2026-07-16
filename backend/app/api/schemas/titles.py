from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GenreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class ReasonOut(BaseModel):
    """Human-readable explanation for a recommendation.

    ``message`` is the user-facing line. ``evidence`` holds structured
    detail for UI polish / debugging (liked title names, directors, tones…).
    """

    code: str = Field(
        description=(
            "Reason kind: because_you_liked | taste_blend | similar_style | "
            "same_director | similar_cast | similar_themes | similar_origin | "
            "similar_language | similar_era | shared_genre | taste_similarity | "
            "genre_fit | discovery"
        )
    )
    message: str = Field(description="Thoughtful, specific sentence shown to the user")
    evidence: dict = Field(
        default_factory=dict,
        description="Structured support: liked_titles, directors, tones, keywords, genres, …",
    )


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
    """Record a title interaction.

    Weights and taste effects: ``docs/TASTE_SIGNALS.md`` and
    ``app.domain.taste_signals``.
    """

    event_type: str = Field(
        pattern=(
            "^(like|dislike|watchlist|not_interested|skip|view|"
            "haven't_seen|rate_1|rate_2|rate_3|rate_4)$"
        ),
        description=(
            "Active signals only. "
            "haven't_seen=0 taste; not_interested=mild−; "
            "rate_1…rate_4=Bad…Favorite; watchlist=mild+; "
            "like/dislike=feed shortcuts. See docs/TASTE_SIGNALS.md."
        ),
    )


class OnboardingReaction(BaseModel):
    """One card decision during onboarding.

    Policy (docs/TASTE_SIGNALS.md):
    - haven't_seen — zero taste signal (does not count as a rating)
    - not_interested — mild negative
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
