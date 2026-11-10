from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.user_repo import UserRepository
from atlas.services.auth import hash_password, seed_demo_users


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
        atlas_auth_secret="reset-email-test-secret-32-bytes-minimum",
        atlas_demo_users="alice:atlas-alice",
        rate_limit_enabled=False,
        login_lockout_enabled=False,
        password_reset_expose_token_in_response=True,
    )
    async with factory() as session:
        repo = UserRepository(session)
        await repo.create("noemail", hash_password("correct-horse-battery"))
        other = await repo.create(
            "other", hash_password("correct-horse-battery"), email="taken@example.com"
        )
        other.email_verified_at = datetime.now(UTC)
        await session.commit()
    await seed_demo_users(factory, settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(auth_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()


async def _auth(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_account_without_email_can_add_one_and_then_reset(client: AsyncClient) -> None:
    headers = await _auth(client, "noemail", "correct-horse-battery")
    me = (await client.get("/api/auth/me", headers=headers)).json()
    assert me["email"] is None
    assert me["demo"] is False

    before = await client.post("/api/auth/forgot-password", json={"username": "noemail"})
    assert before.json()["dev_reset_token"] is None

    saved = await client.post(
        "/api/auth/profile/email", headers=headers, json={"email": " Owner@Example.com "}
    )
    assert saved.status_code == 200, saved.text
    me = (await client.get("/api/auth/me", headers=headers)).json()
    assert me["email"] == "owner@example.com"

    after = await client.post("/api/auth/forgot-password", json={"username": "noemail"})
    assert isinstance(after.json()["dev_reset_token"], str)

    still_signs_in = await client.post(
        "/api/auth/login", json={"username": "noemail", "password": "correct-horse-battery"}
    )
    assert still_signs_in.status_code == 200


@pytest.mark.asyncio
async def test_reset_email_rejects_bad_or_taken_addresses(client: AsyncClient) -> None:
    headers = await _auth(client, "noemail", "correct-horse-battery")
    bad = await client.post("/api/auth/profile/email", headers=headers, json={"email": "nope"})
    assert bad.status_code == 400
    taken = await client.post(
        "/api/auth/profile/email", headers=headers, json={"email": "taken@example.com"}
    )
    assert taken.status_code == 400
    assert taken.json()["detail"] == "That email is already in use."


@pytest.mark.asyncio
async def test_demo_account_cannot_set_a_reset_email(client: AsyncClient) -> None:
    headers = await _auth(client, "alice", "atlas-alice")
    me = (await client.get("/api/auth/me", headers=headers)).json()
    assert me["demo"] is True
    blocked = await client.post(
        "/api/auth/profile/email", headers=headers, json={"email": "mine@example.com"}
    )
    assert blocked.status_code == 400


@pytest.mark.asyncio
async def test_saving_the_same_email_again_is_fine(client: AsyncClient) -> None:
    headers = await _auth(client, "other", "correct-horse-battery")
    again = await client.post(
        "/api/auth/profile/email", headers=headers, json={"email": "taken@example.com"}
    )
    assert again.status_code == 200
