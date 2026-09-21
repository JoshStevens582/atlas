from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.chat import router as chat_router
from atlas.api.routers.documents import router as documents_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.schemas.chat import DocumentOut
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.rag import RagChatService
from atlas.services.rate_limit import RateLimiter, RateLimiterUnavailable, RateLimitExceeded


@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit() -> None:
    settings = Settings(
        rate_limit_ask_per_minute=3,
        rate_limit_ask_per_day=100,
        rate_limit_ask_global_per_day=100,
        rate_limit_window_seconds=60,
    )
    redis = FakeRedis(decode_responses=True)
    limiter = RateLimiter(redis, settings)
    await limiter.hit(username="alice", bucket="ask")
    await limiter.hit(username="alice", bucket="ask")
    await limiter.hit(username="alice", bucket="ask")
    await redis.aclose()


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_minute_limit() -> None:
    settings = Settings(
        rate_limit_ask_per_minute=2,
        rate_limit_ask_per_day=100,
        rate_limit_ask_global_per_day=100,
        rate_limit_window_seconds=60,
    )
    redis = FakeRedis(decode_responses=True)
    limiter = RateLimiter(redis, settings)
    await limiter.hit(username="alice", bucket="ask")
    await limiter.hit(username="alice", bucket="ask")
    with pytest.raises(RateLimitExceeded, match="per minute"):
        await limiter.hit(username="alice", bucket="ask")
    await limiter.hit(username="bob", bucket="ask")
    await redis.aclose()


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_daily_limit() -> None:
    settings = Settings(
        rate_limit_ask_per_minute=100,
        rate_limit_ask_per_day=2,
        rate_limit_ask_global_per_day=100,
        rate_limit_window_seconds=60,
    )
    redis = FakeRedis(decode_responses=True)
    limiter = RateLimiter(redis, settings)
    await limiter.hit(username="alice", bucket="ask")
    await limiter.hit(username="alice", bucket="ask")
    with pytest.raises(RateLimitExceeded, match="per day"):
        await limiter.hit(username="alice", bucket="ask")
    await redis.aclose()


@pytest.mark.asyncio
async def test_rate_limiter_blocks_global_daily_budget() -> None:
    settings = Settings(
        rate_limit_ask_per_minute=100,
        rate_limit_ask_per_day=100,
        rate_limit_ask_global_per_day=2,
        rate_limit_window_seconds=60,
    )
    redis = FakeRedis(decode_responses=True)
    limiter = RateLimiter(redis, settings)
    await limiter.hit(username="alice", bucket="ask")
    await limiter.hit(username="bob", bucket="ask")
    with pytest.raises(RateLimitExceeded, match="app daily budget"):
        await limiter.hit(username="alice", bucket="ask")
    await redis.aclose()


@pytest.mark.asyncio
async def test_rate_limiter_raises_unavailable_on_redis_error() -> None:
    """A RedisError from Redis mid-request (after startup succeeded) must
    surface as RateLimiterUnavailable, not an uncaught RedisError that would
    turn into a raw 500 instead of the documented fail-closed 503.
    """
    settings = Settings(
        rate_limit_ask_per_minute=100,
        rate_limit_ask_per_day=100,
        rate_limit_ask_global_per_day=100,
        rate_limit_window_seconds=60,
    )
    redis = AsyncMock()
    redis.incr = AsyncMock(side_effect=RedisConnectionError("redis gone"))
    limiter = RateLimiter(redis, settings)

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit(username="alice", bucket="ask")


@pytest.fixture
async def limited_client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        openai_api_key="sk-test",
        atlas_auth_secret="rate-test-secret",
        atlas_demo_users="alice:secret-a",
        rate_limit_enabled=True,
        rate_limit_fail_closed=True,
        rate_limit_ask_per_minute=2,
        rate_limit_ask_per_day=100,
        rate_limit_ask_global_per_day=100,
        rate_limit_upload_per_minute=1,
        rate_limit_upload_per_day=100,
        rate_limit_window_seconds=60,
        upload_dir="./data/uploads",
    )
    redis = FakeRedis(decode_responses=True)
    limiter = RateLimiter(redis, settings)
    ingest = AsyncMock(spec=IngestService)
    ingest.ingest_path = AsyncMock(
        return_value=DocumentOut(
            id="doc-1",
            title="Notes",
            original_filename="notes.md",
            chunk_count=1,
            created_at="2026-09-21T00:00:00",
        )
    )

    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.rate_limiter = limiter
    app.state.ingest_service = ingest
    app.state.ingest_queue = None
    app.state.rag_service = RagChatService(
        settings,
        AsyncOpenAI(api_key="sk-test"),
        factory,
        cast(ChromaChunkStore, object()),
        cast(EmbeddingClient, object()),
    )
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(documents_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http
    await redis.aclose()
    await engine.dispose()


async def _auth_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret-a"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_upload_returns_429_when_over_limit(limited_client: AsyncClient) -> None:
    headers = await _auth_headers(limited_client)
    first = await limited_client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("a.md", b"one", "text/markdown")},
    )
    assert first.status_code == 200
    second = await limited_client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("b.md", b"two", "text/markdown")},
    )
    assert second.status_code == 429
    assert "upload" in second.json()["detail"]
    assert second.headers.get("retry-after") is not None


@pytest.mark.asyncio
async def test_ask_returns_429_when_over_limit(limited_client: AsyncClient) -> None:
    headers = await _auth_headers(limited_client)
    body = {"message": "hello"}
    first = await limited_client.post("/api/chat/stream", headers=headers, json=body)
    assert first.status_code == 200
    second = await limited_client.post("/api/chat/stream", headers=headers, json=body)
    assert second.status_code == 200
    third = await limited_client.post("/api/chat/stream", headers=headers, json=body)
    assert third.status_code == 429
    assert "ask" in third.json()["detail"]


@pytest.mark.asyncio
async def test_ask_returns_503_when_redis_dies_mid_request() -> None:
    """End-to-end: RateLimiterUnavailable raised inside deps._enforce_bucket
    must become the same fail-closed 503 as "no Redis client configured",
    not an uncaught 500.
    """
    settings = Settings(
        openai_api_key="sk-test",
        atlas_auth_secret="rate-test-secret-2",
        atlas_demo_users="alice:secret-a",
        rate_limit_enabled=True,
        rate_limit_fail_closed=True,
    )
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    broken_limiter = AsyncMock(spec=RateLimiter)
    broken_limiter.hit = AsyncMock(
        side_effect=RateLimiterUnavailable("Redis is unreachable.")
    )

    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.rate_limiter = broken_limiter
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
    ) as client:
        headers = await _auth_headers(client)
        response = await client.post(
            "/api/chat/stream", headers=headers, json={"message": "hello"}
        )
    await engine.dispose()

    assert response.status_code == 503
    assert "Redis" in response.json()["detail"]


@pytest.mark.asyncio
async def test_ask_returns_503_when_redis_missing_and_fail_closed() -> None:
    settings = Settings(
        openai_api_key="sk-test",
        atlas_auth_secret="rate-test-secret",
        atlas_demo_users="alice:secret-a",
        rate_limit_enabled=True,
        rate_limit_fail_closed=True,
    )
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.rate_limiter = None
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
    ) as client:
        headers = await _auth_headers(client)
        response = await client.post(
            "/api/chat/stream",
            headers=headers,
            json={"message": "hello"},
        )
        assert response.status_code == 503
        assert "Redis" in response.json()["detail"]
    await engine.dispose()
