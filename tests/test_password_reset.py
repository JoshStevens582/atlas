from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.services.password_reset import FORGOT_PASSWORD_MESSAGE, hash_reset_token


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
        atlas_auth_secret="password-reset-test-secret-32-bytes-min",
        atlas_demo_users="",
        rate_limit_enabled=False,
        password_reset_expose_token_in_response=True,
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(auth_router)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http
    await engine.dispose()


@pytest.mark.asyncio
async def test_forgot_password_returns_the_same_message_for_unknown_users(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/forgot-password",
        json={"username": "nobody-here"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == FORGOT_PASSWORD_MESSAGE
    assert response.json()["dev_reset_token"] is None


@pytest.mark.asyncio
async def test_forgot_password_exposes_dev_token_when_user_has_email(client: AsyncClient) -> None:
    created = await client.post(
        "/api/auth/signup",
        json={
            "username": "resetme",
            "password": "correct-horse-battery",
            "email": "reset@example.com",
        },
    )
    assert created.status_code == 200

    response = await client.post(
        "/api/auth/forgot-password",
        json={"username": "resetme"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == FORGOT_PASSWORD_MESSAGE
    token = body["dev_reset_token"]
    assert isinstance(token, str) and len(token) > 20

    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "new-password-ok"},
    )
    assert reset.status_code == 200

    login_old = await client.post(
        "/api/auth/login",
        json={"username": "resetme", "password": "correct-horse-battery"},
    )
    assert login_old.status_code == 401

    login_new = await client.post(
        "/api/auth/login",
        json={"username": "resetme", "password": "new-password-ok"},
    )
    assert login_new.status_code == 200

    reused = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "another-password-ok"},
    )
    assert reused.status_code == 400


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_email(client: AsyncClient) -> None:
    first = await client.post(
        "/api/auth/signup",
        json={
            "username": "user_a",
            "password": "correct-horse-battery",
            "email": "same@example.com",
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/auth/signup",
        json={
            "username": "user_b",
            "password": "correct-horse-battery",
            "email": "same@example.com",
        },
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_hash_reset_token_is_stable() -> None:
    assert hash_reset_token("abc") == hash_reset_token("abc")
    assert hash_reset_token("abc") != hash_reset_token("def")
