from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

# Embedding dimension for MVP content vectors (structured + text hash features).
# Re-embed in batch if we change the model later.
EMBEDDING_DIM = 384


class Title(Base):
    __tablename__ = "titles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # movie | tv
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    original_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    runtime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    popularity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vote_average: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vote_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    poster_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    backdrop_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    external_tmdb_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    # Optional free-form attributes for franchise, mood tags, etc.
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    genres: Mapped[list[Genre]] = relationship(
        secondary="title_genres", back_populates="titles", lazy="selectin"
    )
    keywords: Mapped[list[Keyword]] = relationship(
        secondary="title_keywords", back_populates="titles", lazy="selectin"
    )
    credits: Mapped[list[Credit]] = relationship(back_populates="title", lazy="selectin")


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    external_tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)

    titles: Mapped[list[Title]] = relationship(
        secondary="title_genres", back_populates="genres", lazy="selectin"
    )


class TitleGenre(Base):
    __tablename__ = "title_genres"
    __table_args__ = (UniqueConstraint("title_id", "genre_id", name="uq_title_genre"),)

    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    genre_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True
    )


class Person(Base):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    external_tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    profile_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    credits: Mapped[list[Credit]] = relationship(back_populates="person")


class Credit(Base):
    __tablename__ = "credits"
    __table_args__ = (
        UniqueConstraint(
            "title_id", "person_id", "credit_type", "job", name="uq_credit_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id", ondelete="CASCADE"), index=True, nullable=False
    )
    credit_type: Mapped[str] = mapped_column(String(16), nullable=False)  # cast | crew
    job: Mapped[str | None] = mapped_column(String(120), nullable=True)  # Director, Writer, ...
    character: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    title: Mapped[Title] = relationship(back_populates="credits")
    person: Mapped[Person] = relationship(back_populates="credits")


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    external_tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)

    titles: Mapped[list[Title]] = relationship(
        secondary="title_keywords", back_populates="keywords", lazy="selectin"
    )


class TitleKeyword(Base):
    __tablename__ = "title_keywords"
    __table_args__ = (UniqueConstraint("title_id", "keyword_id", name="uq_title_keyword"),)

    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    keyword_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True
    )
