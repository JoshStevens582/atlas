from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.user_repo import UserRepository
from atlas.services.auth import (
    JWT_ALGORITHM,
    AuthError,
    authenticate_user,
    hash_password,
    issue_access_token,
    seed_demo_users,
    signup_user,
    verify_access_token,
    verify_password,
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


def test_hash_password_is_not_the_plaintext_and_verifies() -> None:
    hashed = hash_password("correct-horse-battery")
    assert hashed != "correct-horse-battery"
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong-password", hashed)


def test_verify_password_rejects_malformed_hash() -> None:
    assert not verify_password("anything", "not-a-real-bcrypt-hash")


async def test_signup_then_login_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    username = await signup_user(session_factory, "alice", "correct-horse-battery")
    assert username == "alice"

    assert await authenticate_user(session_factory, "alice", "wrong-password") is None
    assert (
        await authenticate_user(session_factory, "alice", "correct-horse-battery") == "alice"
    )


async def test_signup_rejects_short_username_or_password(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(AuthError, match="Username"):
        await signup_user(session_factory, "ab", "correct-horse-battery")
    with pytest.raises(AuthError, match="Password"):
        await signup_user(session_factory, "alice", "short")


async def test_signup_rejects_duplicate_username(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_user(session_factory, "alice", "correct-horse-battery")
    with pytest.raises(AuthError, match="already taken"):
        await signup_user(session_factory, "alice", "another-password")


async def test_login_unknown_username_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await authenticate_user(session_factory, "nobody", "whatever1") is None


async def test_signup_race_between_check_and_insert_is_caught(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two signups for the same username can both pass the "does this
    username exist" check before either commits. The DB's unique index is
    the real guard; this proves that race still surfaces as a clean
    AuthError instead of an unhandled IntegrityError."""
    await signup_user(session_factory, "alice", "correct-horse-battery")

    async def _always_missing(self: UserRepository, username: str) -> None:
        return None

    monkeypatch.setattr(UserRepository, "get_by_username", _always_missing)
    with pytest.raises(AuthError, match="already taken"):
        await signup_user(session_factory, "alice", "another-password")


@pytest.mark.parametrize(
    "raw_demo_users",
    [
        "",
        "not-a-pair",
        "alice:",
        ":atlas-alice",
    ],
)
async def test_seed_demo_users_skips_malformed_entries(
    session_factory: async_sessionmaker[AsyncSession],
    raw_demo_users: str,
) -> None:
    settings = Settings(atlas_auth_secret="unit-test-secret", atlas_demo_users=raw_demo_users)
    await seed_demo_users(session_factory, settings)  # must not raise
    assert await authenticate_user(session_factory, "alice", "atlas-alice") is None


async def test_seed_demo_users_race_between_check_and_insert_is_caught(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two processes/workers seeding on startup at the same time can both
    see 'no such user yet' before either commits; the seed loop must not
    crash the whole startup over that race."""
    settings = Settings(atlas_auth_secret="unit-test-secret", atlas_demo_users="alice:atlas-alice")
    await seed_demo_users(session_factory, settings)

    async def _always_missing(self: UserRepository, username: str) -> None:
        return None

    monkeypatch.setattr(UserRepository, "get_by_username", _always_missing)
    await seed_demo_users(session_factory, settings)  # must not raise
    monkeypatch.undo()
    assert await authenticate_user(session_factory, "alice", "atlas-alice") == "alice"


async def test_seed_demo_users_creates_real_rows_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(
        atlas_auth_secret="unit-test-secret",
        atlas_demo_users="alice:atlas-alice|bob:atlas-bob",
    )
    await seed_demo_users(session_factory, settings)
    assert await authenticate_user(session_factory, "alice", "atlas-alice") == "alice"
    assert await authenticate_user(session_factory, "bob", "atlas-bob") == "bob"

    # Running it again (e.g. app restart) must not blow up on the unique
    # username constraint or overwrite an already-real account.
    await seed_demo_users(session_factory, settings)
    assert await authenticate_user(session_factory, "alice", "atlas-alice") == "alice"


def test_token_round_trip() -> None:
    settings = Settings(atlas_auth_secret="unit-test-secret")
    token = issue_access_token(settings, "alice")
    assert verify_access_token(settings, token) == "alice"
    assert verify_access_token(settings, "not-a-token") is None
    assert verify_access_token(settings, "") is None


def test_verify_rejects_empty_secret() -> None:
    settings = Settings(atlas_auth_secret="unit-test-secret")
    token = issue_access_token(settings, "alice")
    blank = Settings(atlas_auth_secret="")
    assert verify_access_token(blank, token) is None


def test_token_from_other_secret_is_rejected() -> None:
    settings = Settings(atlas_auth_secret="unit-test-secret")
    other = Settings(atlas_auth_secret="different-secret")
    token = issue_access_token(settings, "alice")
    assert verify_access_token(other, token) is None


@pytest.mark.parametrize("payload", [{"sub": ""}, {"sub": 123}, {}])
def test_verify_rejects_missing_or_empty_subject(payload: dict[str, object]) -> None:
    settings = Settings(atlas_auth_secret="unit-test-secret")
    token = jwt.encode(payload, settings.atlas_auth_secret, algorithm=JWT_ALGORITHM)
    assert verify_access_token(settings, token) is None


def test_verify_rejects_expired_token() -> None:
    settings = Settings(atlas_auth_secret="unit-test-secret")
    expired = datetime.now(UTC) - timedelta(days=1)
    token = jwt.encode(
        {"sub": "alice", "exp": expired},
        settings.atlas_auth_secret,
        algorithm=JWT_ALGORITHM,
    )
    assert verify_access_token(settings, token) is None


def test_verify_rejects_alg_none_forged_token() -> None:
    """A real, historical JWT vulnerability class: if the verifier doesn't
    pin an algorithm, a forged token can set alg to "none" and skip
    signature verification entirely. Pinning algorithms=[JWT_ALGORITHM] in
    verify_access_token is what closes this off."""
    settings = Settings(atlas_auth_secret="unit-test-secret")
    forged = jwt.encode({"sub": "alice"}, "", algorithm="none")
    assert verify_access_token(settings, forged) is None
