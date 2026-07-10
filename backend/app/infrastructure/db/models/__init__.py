"""SQLAlchemy ORM models.

Import all models here so Alembic and metadata discovery stay complete.
"""

from app.infrastructure.db.models.catalog import (
    Credit,
    Genre,
    Keyword,
    Person,
    Title,
    TitleGenre,
    TitleKeyword,
)
from app.infrastructure.db.models.interaction import InteractionEvent, UserTitleState
from app.infrastructure.db.models.taste import TasteProfile
from app.infrastructure.db.models.user import RefreshToken, User

__all__ = [
    "User",
    "RefreshToken",
    "Title",
    "Genre",
    "TitleGenre",
    "Person",
    "Credit",
    "Keyword",
    "TitleKeyword",
    "InteractionEvent",
    "UserTitleState",
    "TasteProfile",
]
