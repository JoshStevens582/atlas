from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.services.email_verification import RESEND_VERIFICATION_MESSAGE


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
        atlas_auth_secret="email-verification-test-secret-32b",
        atlas_demo_users="",
        rate_limit_enabled=False,
        login_lockout_enabled=False,
        email_verification_expose_token_in_response=True,
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
async def test_signup_without_email_issues_token_immediately(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"username": "noemail", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "noemail"
    assert isinstance(body["access_token"], str)
    assert body.get("message") is None


@pytest.mark.asyncio
async def test_signup_with_email_requires_verification_before_login(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/auth/signup",
        json={
            "username": "needsverify",
            "password": "correct-horse-battery",
            "email": "verify@example.com",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["access_token"] is None
    assert "verification" in (body.get("message") or "").lower()
    token = body["dev_verify_token"]
    assert isinstance(token, str) and len(token) > 20

    blocked = await client.post(
        "/api/auth/login",
        json={"username": "needsverify", "password": "correct-horse-battery"},
    )
    assert blocked.status_code == 403

    verified = await client.post("/api/auth/verify-email", json={"token": token})
    assert verified.status_code == 200

    login_ok = await client.post(
        "/api/auth/login",
        json={"username": "needsverify", "password": "correct-horse-battery"},
    )
    assert login_ok.status_code == 200


@pytest.mark.asyncio
async def test_resend_verification_uses_the_same_message_for_unknown_users(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/resend-verification",
        json={"username": "nobody"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == RESEND_VERIFICATION_MESSAGE
