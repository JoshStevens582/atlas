from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.chat import router as chat_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.auth import seed_demo_users
from atlas.services.embeddings import EmbeddingClient
from atlas.services.rag import RagChatService


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
        rate_limit_enabled=False,
    )
    await seed_demo_users(factory, settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.rag_service = RagChatService(
        settings,
        AsyncOpenAI(api_key="sk-test"),
        factory,
        cast(ChromaChunkStore, object()),
        cast(EmbeddingClient, object()),
    )
    app.include_router(auth_router)
    app.include_router(chat_router)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http
    await engine.dispose()


async def _login(client: AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


@pytest.mark.asyncio
async def test_threads_require_login(client: AsyncClient) -> None:
    response = await client.get("/api/threads")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_bad_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "nope"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


@pytest.mark.asyncio
async def test_signup_creates_a_real_account(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"username": "carol", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "carol"
    assert isinstance(body["access_token"], str)

    # The new account can log in normally afterwards.
    login_response = await client.post(
        "/api/auth/login",
        json={"username": "carol", "password": "correct-horse-battery"},
    )
    assert login_response.status_code == 200


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_username(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"username": "alice", "password": "another-password"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_signup_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"username": "dave", "password": "short"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_requires_auth_secret_configured() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        atlas_auth_secret="",
        atlas_demo_users="alice:secret-a",
        rate_limit_enabled=False,
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(auth_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(
            "/api/auth/login", json={"username": "alice", "password": "secret-a"}
        )
    await engine.dispose()
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_demo_login_503_when_no_demo_account_configured() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        atlas_auth_secret="http-test-secret",
        atlas_demo_users="",
        rate_limit_enabled=False,
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(auth_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/api/auth/demo")
    await engine.dispose()
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_demo_login_needs_no_credentials(client: AsyncClient) -> None:
    response = await client.post("/api/auth/demo")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"

    threads = await client.get(
        "/api/threads",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert threads.status_code == 200


@pytest.mark.asyncio
async def test_alice_cannot_read_bob_thread(client: AsyncClient) -> None:
    alice_token = await _login(client, "alice", "secret-a")
    bob_token = await _login(client, "bob", "secret-b")

    created = await client.post(
        "/api/threads",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert created.status_code == 200
    thread_id = created.json()["id"]

    bob_list = await client.get(
        "/api/threads",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert bob_list.status_code == 200
    assert bob_list.json() == []

    forbidden = await client.get(
        f"/api/threads/{thread_id}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert forbidden.status_code == 403

    missing = await client.get(
        "/api/threads/does-not-exist",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert missing.status_code == 404

    junk = await client.get(
        "/api/threads",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert junk.status_code == 401
