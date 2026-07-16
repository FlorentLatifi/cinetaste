from __future__ import annotations

from datetime import date, datetime
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
            "genre_fit | discovery | hidden_gem"
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


class CreditOut(BaseModel):
    name: str
    credit_type: str  # cast | crew
    job: str | None = None
    character: str | None = None
    billing_order: int | None = None
    profile_url: str | None = None


class TitleDetailOut(TitleSummaryOut):
    """Full title card with ordered cast/crew for the detail page."""

    credits: list[CreditOut] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @classmethod
    def from_title_detail(cls, title) -> TitleDetailOut:
        base = TitleSummaryOut.from_title(title)
        credits: list[CreditOut] = []
        for c in title.credits or []:
            person = getattr(c, "person", None)
            name = person.name if person is not None else "Unknown"
            profile = None
            if person is not None and person.profile_path:
                path = person.profile_path
                profile = (
                    path
                    if path.startswith("http")
                    else f"https://image.tmdb.org/t/p/w185{path}"
                )
            credits.append(
                CreditOut(
                    name=name,
                    credit_type=c.credit_type,
                    job=c.job,
                    character=c.character,
                    billing_order=c.billing_order,
                    profile_url=profile,
                )
            )
        # Cast by billing, then crew directors/writers first
        credits.sort(
            key=lambda x: (
                0 if x.credit_type == "cast" else 1,
                x.billing_order if x.billing_order is not None else 999,
                x.name.lower(),
            )
        )
        keywords = [k.name for k in (title.keywords or [])][:20]
        return cls(**base.model_dump(), credits=credits, keywords=keywords)


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
            "^(like|dislike|watchlist|not_interested|clear|skip|view|"
            "haven't_seen|rate_1|rate_2|rate_3|rate_4|"
            "watched|watched_liked|watched_disliked)$"
        ),
        description=(
            "Active signals only. "
            "haven't_seen=0 taste; not_interested=mild−; "
            "rate_1…rate_4=Bad…Favorite; watchlist=mild+; "
            "like/dislike=feed shortcuts; clear=undo title state + prior taste; "
            "watched / watched_liked / watched_disliked=post-watch completion. "
            "See docs/TASTE_SIGNALS.md."
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


class HistoryItemOut(BaseModel):
    """One durable user–title relationship for the History page."""

    title: TitleSummaryOut
    state: str
    label: str
    updated_at: datetime


class ProviderOfferOut(BaseModel):
    provider_id: int
    name: str
    logo_url: str | None = None
    display_priority: int = 999


class WhereToWatchOut(BaseModel):
    """Streaming / purchase availability for one country (TMDb JustWatch data)."""

    region: str
    link: str | None = None
    flatrate: list[ProviderOfferOut] = Field(default_factory=list)
    free: list[ProviderOfferOut] = Field(default_factory=list)
    ads: list[ProviderOfferOut] = Field(default_factory=list)
    rent: list[ProviderOfferOut] = Field(default_factory=list)
    buy: list[ProviderOfferOut] = Field(default_factory=list)
    available: bool = False
    attribution: str = "Streaming data by JustWatch via TMDb"
    source: str = "tmdb"
