from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.config import Settings
from atlas.db.models import Base


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = FastAPI()
    app.state.settings = Settings(
        atlas_auth_secret="demo-unavailable-test-key-long-enough",
        # a demo account is configured, but it was never created in the database
        atlas_demo_users="ghost:ghost-password",
        rate_limit_enabled=False,
    )
    app.state.session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    app.include_router(auth_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()


@pytest.mark.asyncio
async def test_demo_login_says_503_when_the_configured_demo_account_does_not_exist(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/auth/demo")

    assert response.status_code == 503
    assert response.json()["detail"] == "Demo account is not available."
