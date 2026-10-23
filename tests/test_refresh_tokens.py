"""Refresh token rotation, logout, and disable revokes sessions."""

from collections.abc import AsyncIterator

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.services.auth import JWT_ALGORITHM, seed_demo_users


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
        atlas_auth_secret="refresh-test-secret-32-bytes-min!!",
        atlas_demo_users="alice:secret-a",
        access_token_ttl_seconds=900,
        refresh_token_ttl_seconds=60 * 60 * 24,
        rate_limit_enabled=False,
        login_lockout_enabled=False,
    )
    await seed_demo_users(factory, settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(auth_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()


@pytest.mark.asyncio
async def test_login_returns_refresh_and_refresh_issues_new_access(client: AsyncClient) -> None:
    login = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret-a"},
    )
    assert login.status_code == 200
    body = login.json()
    access = body["access_token"]
    refresh = body["refresh_token"]
    assert isinstance(access, str)
    assert isinstance(refresh, str)

    payload = jwt.decode(
        access,
        "refresh-test-secret-32-bytes-min!!",
        algorithms=[JWT_ALGORITHM],
    )
    exp = payload["exp"]
    iat = payload["iat"]
    assert exp - iat == 900

    rotated = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert rotated.status_code == 200
    new_body = rotated.json()
    assert new_body["refresh_token"] != refresh
    assert isinstance(new_body["access_token"], str)

    stale = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert stale.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh(client: AsyncClient) -> None:
    login = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret-a"},
    )
    refresh = login.json()["refresh_token"]
    logout = await client.post("/api/auth/logout", json={"refresh_token": refresh})
    assert logout.status_code == 200
    again = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert again.status_code == 401

