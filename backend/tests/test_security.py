from uuid import uuid4

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)


def _settings() -> Settings:
    return Settings(
        jwt_secret="unit-test-secret-key-at-least-32-chars!",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
    )


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("secure-password-123")
    assert hashed != "secure-password-123"
    assert verify_password("secure-password-123", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_roundtrip() -> None:
    settings = _settings()
    user_id = uuid4()
    token = create_access_token(user_id=user_id, settings=settings)
    payload = decode_access_token(token, settings)
    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"


def test_refresh_token_hash_is_stable() -> None:
    raw = generate_refresh_token()
    assert hash_token(raw) == hash_token(raw)
    assert len(hash_token(raw)) == 64


def test_bcrypt_uses_first_72_bytes_consistently() -> None:
    long_password = "p" * 100
    hashed = hash_password(long_password)
    assert hashed.startswith("$2b$12$")
    assert verify_password(long_password, hashed)


def test_verify_rejects_malformed_hash() -> None:
    assert not verify_password("anything", "not-a-bcrypt-hash")
