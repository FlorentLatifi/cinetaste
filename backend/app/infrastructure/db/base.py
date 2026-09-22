from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # Fetch server-generated values (created_at/updated_at defaults, onupdate
    # now()) with RETURNING on INSERT and UPDATE. Otherwise they are expired
    # after a flush and reading them needs a lazy load, which an async session
    # can't do (MissingGreenlet) — e.g. returning profile.updated_at after a
    # taste import used to crash.
    __mapper_args__ = {"eager_defaults": True}
