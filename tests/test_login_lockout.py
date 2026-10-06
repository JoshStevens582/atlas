from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.services.auth import seed_demo_users
from atlas.services.login_lockout import (
    AccountLocked,
    LoginLockout,
    LoginLockoutUnavailable,
)
from atlas.services.rate_limit import RateLimiter


@pytest.fixture
async def auth_client() -> AsyncIterator[tuple[AsyncClient, FakeRedis]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        atlas_auth_secret="lockout-test-secret-32-bytes-minimum",
        atlas_demo_users="alice:secret-a",
        rate_limit_enabled=False,
        login_lockout_enabled=True,
        login_lockout_fail_closed=True,
        login_lockout_max_failures=3,
        login_lockout_failure_window_seconds=600,
        login_lockout_duration_seconds=120,
    )
    await seed_demo_users(factory, settings)
    redis = FakeRedis(decode_responses=True)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.rate_limiter = RateLimiter(redis, settings)
    app.state.login_lockout = LoginLockout(redis, settings)
    app.include_router(auth_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, redis
    await engine.dispose()


@pytest.mark.asyncio
async def test_lockout_blocks_after_max_failures(
    auth_client: tuple[AsyncClient, FakeRedis],
) -> None:
    client, _redis = auth_client
    for _ in range(2):
        response = await client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong"},
        )
        assert response.status_code == 401

    locked = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong"},
    )
    assert locked.status_code == 429
    assert "failed login attempts" in locked.json()["detail"].lower()
    assert locked.headers["retry-after"] == "120"

    still_locked = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret-a"},
    )
    assert still_locked.status_code == 429


@pytest.mark.asyncio
async def test_successful_login_clears_failures(auth_client: tuple[AsyncClient, FakeRedis]) -> None:
    client, _redis = auth_client
    await client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    await client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})

    ok = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret-a"},
    )
    assert ok.status_code == 200

    for _ in range(2):
        bad = await client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong"},
        )
        assert bad.status_code == 401


@pytest.mark.asyncio
async def test_login_lockout_unit_assert_not_locked() -> None:
    redis = FakeRedis(decode_responses=True)
    settings = Settings(
        login_lockout_max_failures=2,
        login_lockout_duration_seconds=90,
        login_lockout_failure_window_seconds=300,
    )
    lockout = LoginLockout(redis, settings)
    await lockout.record_failure("bob")
    assert await lockout.record_failure("bob") is True

    with pytest.raises(AccountLocked) as exc_info:
        await lockout.assert_not_locked("bob")
    assert exc_info.value.retry_after_seconds >= 1

    await lockout.record_success("bob")
    await lockout.assert_not_locked("bob")


class BrokenRedis:
    async def ttl(self, _key: str) -> int:
        raise RedisError("down")

    async def incr(self, _key: str) -> int:
        raise RedisError("down")

    async def expire(self, _key: str, _seconds: int) -> bool:
        raise RedisError("down")

    async def set(self, *_args: Any, **_kwargs: Any) -> bool:
        raise RedisError("down")

    async def delete(self, *_keys: str) -> int:
        raise RedisError("down")


@pytest.mark.asyncio
async def test_login_lockout_unavailable_on_redis_error() -> None:
    lockout = LoginLockout(cast(Redis, BrokenRedis()), Settings())
    with pytest.raises(LoginLockoutUnavailable):
        await lockout.record_failure("carol")
