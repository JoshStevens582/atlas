"""Authenticator setup, sign-in challenge, recovery codes, and admin clear."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.admin import router as admin_router
from atlas.api.routers.auth import router as auth_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.services.admin import AdminNotFoundError, apply_admin_usernames, clear_user_mfa
from atlas.services.auth import AuthError, seed_demo_users
from atlas.services.mfa import (
    MfaRejected,
    accept_second_factor,
    begin_mfa_setup,
    confirm_mfa_setup,
    decrypt_totp_secret,
    disable_mfa,
    encrypt_totp_secret,
    hash_recovery_code,
    regenerate_recovery_codes,
)
from atlas.services.totp import current_totp_code


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


def _settings() -> Settings:
    return Settings(
        atlas_auth_secret="unit-test-secret",
        atlas_demo_users="alice:atlas-alice",
        rate_limit_enabled=False,
        login_lockout_enabled=False,
    )


async def test_encrypt_round_trip_and_wrong_key_does_not_reveal_the_secret() -> None:
    settings = _settings()
    stored = encrypt_totp_secret(settings, "MYSECRET")
    assert "MYSECRET" not in stored
    assert decrypt_totp_secret(settings, stored) == "MYSECRET"
    other = Settings(atlas_auth_secret="different-secret")
    with pytest.raises(Exception, match="could not be read"):
        decrypt_totp_secret(other, stored)
    with pytest.raises(Exception, match="could not be read"):
        decrypt_totp_secret(settings, "not-a-fernet-token")


async def test_same_code_cannot_be_reused_and_recovery_code_works_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    await seed_demo_users(session_factory, settings)
    moment = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    setup = await begin_mfa_setup(
        session_factory,
        settings,
        username="alice",
        password="atlas-alice",
    )
    code = current_totp_code(setup.secret, now=int(moment.timestamp()))
    recovery_codes = await confirm_mfa_setup(
        session_factory,
        settings,
        username="alice",
        code=code,
        now=moment,
    )
    assert len(recovery_codes) == 8
    assert setup.qr_data_uri.startswith("data:image/svg+xml")

    with pytest.raises(MfaRejected, match="already used"):
        await accept_second_factor(
            session_factory,
            settings,
            username="alice",
            code=code,
            now=moment,
        )

    await accept_second_factor(
        session_factory,
        settings,
        username="alice",
        code=recovery_codes[0],
        now=moment,
    )
    with pytest.raises(MfaRejected, match="did not match"):
        await accept_second_factor(
            session_factory,
            settings,
            username="alice",
            code=recovery_codes[0].upper(),
            now=moment,
        )

    await disable_mfa(
        session_factory,
        settings,
        username="alice",
        password="atlas-alice",
        code=recovery_codes[1],
        now=moment,
    )
    with pytest.raises(Exception, match="already off"):
        await disable_mfa(
            session_factory,
            settings,
            username="alice",
            password="atlas-alice",
            code=recovery_codes[2],
            now=moment,
        )


async def test_second_factor_edges_and_a_second_setup_is_refused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    await seed_demo_users(session_factory, settings)
    moment = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="8 characters"):
        hash_recovery_code("abc")
    with pytest.raises(AuthError, match="not found"):
        await begin_mfa_setup(
            session_factory, settings, username="nobody", password="atlas-alice"
        )
    with pytest.raises(MfaRejected, match="did not match"):
        await accept_second_factor(
            session_factory, settings, username="alice", code="123456", now=moment
        )

    setup = await begin_mfa_setup(
        session_factory, settings, username="alice", password="atlas-alice"
    )
    real = current_totp_code(setup.secret, now=int(moment.timestamp()))
    wrong = "000000" if real != "000000" else "111111"
    with pytest.raises(MfaRejected, match="did not match"):
        await confirm_mfa_setup(
            session_factory, settings, username="alice", code=wrong, now=moment
        )
    await confirm_mfa_setup(
        session_factory, settings, username="alice", code=real, now=moment
    )
    with pytest.raises(AuthError, match="already on"):
        await begin_mfa_setup(
            session_factory, settings, username="alice", password="atlas-alice"
        )
    with pytest.raises(AuthError, match="already on"):
        await confirm_mfa_setup(
            session_factory, settings, username="alice", code=real, now=moment
        )
    with pytest.raises(MfaRejected, match="Password"):
        await disable_mfa(
            session_factory,
            settings,
            username="alice",
            password="nope",
            code=real,
            now=moment,
        )
    with pytest.raises(AdminNotFoundError):
        await clear_user_mfa(
            session_factory, actor_username="alice", username="   "
        )
    with pytest.raises(AdminNotFoundError):
        await clear_user_mfa(
            session_factory, actor_username="alice", username="missing"
        )


async def test_setup_rejects_a_wrong_password_and_confirm_before_setup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    await seed_demo_users(session_factory, settings)
    with pytest.raises(MfaRejected, match="Password"):
        await begin_mfa_setup(
            session_factory,
            settings,
            username="alice",
            password="nope",
        )
    with pytest.raises(Exception, match="Start authenticator"):
        await confirm_mfa_setup(
            session_factory,
            settings,
            username="alice",
            code="123456",
        )


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        openai_api_key="",
        atlas_auth_secret="http-test-secret",
        atlas_demo_users="alice:secret-a|bob:secret-b",
        atlas_admin_usernames="alice",
        rate_limit_enabled=False,
        login_lockout_enabled=False,
    )
    await seed_demo_users(factory, settings)
    await apply_admin_usernames(factory, settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(auth_router)
    app.include_router(admin_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()


async def _token(client: AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mfa_required"] is False
    token = body["access_token"]
    assert isinstance(token, str)
    return token


async def _enable_mfa(
    client: AsyncClient,
    username: str,
    password: str,
    *,
    totp_now: int | None = None,
) -> tuple[list[str], str]:
    token = await _token(client, username, password)
    headers = {"Authorization": f"Bearer {token}"}
    setup = await client.post(
        "/api/auth/mfa/setup",
        headers=headers,
        json={"password": password},
    )
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert isinstance(secret, str)
    moment = totp_now if totp_now is not None else int(datetime.now(UTC).timestamp())
    code = current_totp_code(secret, now=moment)
    confirmed = await client.post(
        "/api/auth/mfa/confirm",
        headers=headers,
        json={"code": code},
    )
    assert confirmed.status_code == 200
    codes = confirmed.json()["recovery_codes"]
    assert isinstance(codes, list)
    assert len(codes) == 8
    return [str(item) for item in codes], secret


async def _turn_on(client: AsyncClient, username: str, password: str) -> list[str]:
    codes, _secret = await _enable_mfa(client, username, password)
    return codes


@pytest.mark.asyncio
async def test_login_waits_for_a_code_and_a_recovery_code_opens_a_session(
    client: AsyncClient,
) -> None:
    recovery_codes = await _turn_on(client, "bob", "secret-b")

    challenged = await client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "secret-b"},
    )
    assert challenged.status_code == 200
    body = challenged.json()
    assert body["access_token"] is None
    assert body["mfa_required"] is True
    mfa_token = body["mfa_token"]
    assert isinstance(mfa_token, str)

    rejected = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert rejected.status_code == 401

    wrong = await client.post(
        "/api/auth/login/mfa",
        json={"mfa_token": mfa_token, "code": "000000"},
    )
    assert wrong.status_code == 401

    signed_in = await client.post(
        "/api/auth/login/mfa",
        json={"mfa_token": mfa_token, "code": recovery_codes[0]},
    )
    assert signed_in.status_code == 200
    access = signed_in.json()["access_token"]
    assert isinstance(access, str)
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["mfa_enabled"] is True

    reused = await client.post(
        "/api/auth/login/mfa",
        json={"mfa_token": mfa_token, "code": recovery_codes[0]},
    )
    assert reused.status_code == 401


@pytest.mark.asyncio
async def test_unfinished_setup_does_not_block_login_and_admin_can_clear_someone_else(
    client: AsyncClient,
) -> None:
    bob = await _token(client, "bob", "secret-b")
    setup = await client.post(
        "/api/auth/mfa/setup",
        headers={"Authorization": f"Bearer {bob}"},
        json={"password": "secret-b"},
    )
    assert setup.status_code == 200
    still_open = await client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "secret-b"},
    )
    assert still_open.json()["access_token"]
    assert still_open.json()["mfa_required"] is False

    bad_password = await client.post(
        "/api/auth/mfa/setup",
        headers={"Authorization": f"Bearer {bob}"},
        json={"password": "wrong-password"},
    )
    assert bad_password.status_code == 400

    recovery_codes = await _turn_on(client, "bob", "secret-b")
    alice = await _token(client, "alice", "secret-a")
    own = await client.post(
        "/api/admin/users/alice/mfa/reset",
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert own.status_code == 400

    forbidden = await client.post(
        "/api/admin/users/alice/mfa/reset",
        headers={"Authorization": f"Bearer {bob}"},
    )
    assert forbidden.status_code == 403

    cleared = await client.post(
        "/api/admin/users/bob/mfa/reset",
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["mfa_enabled"] is False

    bob_login = await client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "secret-b"},
    )
    assert bob_login.json()["mfa_required"] is False
    assert isinstance(bob_login.json()["access_token"], str)
    assert recovery_codes


@pytest.mark.asyncio
async def test_login_mfa_rejects_an_access_token_and_an_expired_challenge(
    client: AsyncClient,
) -> None:
    access = await _token(client, "alice", "secret-a")
    rejected = await client.post(
        "/api/auth/login/mfa",
        json={"mfa_token": access, "code": "123456"},
    )
    assert rejected.status_code == 401
    assert "expired" in rejected.json()["detail"]

    missing = await client.post("/api/auth/mfa/setup", json={"password": "secret-a"})
    assert missing.status_code == 401


@pytest.mark.asyncio
async def test_disable_from_the_api_returns_password_only_sign_in(client: AsyncClient) -> None:
    recovery_codes = await _turn_on(client, "bob", "secret-b")
    challenged = await client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "secret-b"},
    )
    signed_in = await client.post(
        "/api/auth/login/mfa",
        json={"mfa_token": challenged.json()["mfa_token"], "code": recovery_codes[0]},
    )
    access = signed_in.json()["access_token"]
    turned_off = await client.post(
        "/api/auth/mfa/disable",
        headers={"Authorization": f"Bearer {access}"},
        json={"password": "secret-b", "code": recovery_codes[1]},
    )
    assert turned_off.status_code == 200
    password_only = await client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "secret-b"},
    )
    assert password_only.json()["mfa_required"] is False
    assert isinstance(password_only.json()["access_token"], str)


@pytest.mark.asyncio
async def test_regenerate_recovery_codes_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    settings = Settings(
        atlas_auth_secret=settings.atlas_auth_secret,
        atlas_demo_users="bob:secret-b",
        rate_limit_enabled=False,
        login_lockout_enabled=False,
    )
    await seed_demo_users(session_factory, settings)
    moment = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    setup = await begin_mfa_setup(
        session_factory,
        settings,
        username="bob",
        password="secret-b",
    )
    code = current_totp_code(setup.secret, now=int(moment.timestamp()))
    old_codes = await confirm_mfa_setup(
        session_factory,
        settings,
        username="bob",
        code=code,
        now=moment,
    )
    later = moment.timestamp() + 35
    app_code = current_totp_code(setup.secret, now=int(later))
    with pytest.raises(MfaRejected):
        await regenerate_recovery_codes(
            session_factory,
            settings,
            username="bob",
            password="secret-b",
            code=old_codes[0],
        )
    new_codes = await regenerate_recovery_codes(
        session_factory,
        settings,
        username="bob",
        password="secret-b",
        code=app_code,
        now=datetime.fromtimestamp(later, tz=UTC),
    )
    assert len(new_codes) == 8
    with pytest.raises(MfaRejected):
        await accept_second_factor(
            session_factory,
            settings,
            username="bob",
            code=old_codes[1],
        )
    await accept_second_factor(
        session_factory,
        settings,
        username="bob",
        code=new_codes[0],
        now=datetime.fromtimestamp(later + 35, tz=UTC),
    )
