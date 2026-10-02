from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.health import router as health_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import DocumentRepository


class StubChunkStore:
    def count(self) -> int:
        return 7


class OkRedis:
    async def ping(self) -> bool:
        return True


class SilentRedis:
    async def ping(self) -> bool:
        return False


class FailingRedis:
    async def ping(self) -> bool:
        raise ConnectionError("connection refused")


class NoPingRedis:
    pass


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


def _health_app(
    factory: async_sessionmaker[AsyncSession],
    openai_api_key: str,
) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(openai_api_key=openai_api_key)
    app.state.session_factory = factory
    app.state.chunk_store = cast(ChromaChunkStore, StubChunkStore())
    app.include_router(health_router)
    return app


class LockedDatabase:
    async def __aenter__(self) -> "LockedDatabase":
        raise RuntimeError("database is locked")

    async def __aexit__(self, *_args: object) -> None:
        return None


class LockedDatabaseFactory:
    def __call__(self) -> LockedDatabase:
        return LockedDatabase()


def _ready_app(
    factory: object | None,
    redis_client: object | None,
    *,
    set_redis_client: bool = True,
) -> FastAPI:
    app = FastAPI()
    if factory is not None:
        app.state.session_factory = factory
    if set_redis_client:
        app.state.redis_client = redis_client
    app.include_router(health_router)
    return app


async def _get_json(app: FastAPI, path: str) -> tuple[int, dict[str, object]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)
    body: dict[str, object] = response.json()
    return response.status_code, body


async def _get_health(
    factory: async_sessionmaker[AsyncSession], openai_api_key: str
) -> dict[str, object]:
    status_code, body = await _get_json(_health_app(factory, openai_api_key), "/api/health")
    assert status_code == 200
    return body


@pytest.mark.asyncio
async def test_health_reports_counts_and_that_the_openai_key_is_set(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        await DocumentRepository(session).create_document(
            title="Demo", original_filename="demo.md", chunk_count=3
        )

    body = await _get_health(factory, openai_api_key="sk-configured")

    assert body == {
        "status": "ok",
        "openai_configured": True,
        "vector_store": "chromadb",
        "document_count": 1,
        "chunk_count": 7,
    }


@pytest.mark.asyncio
async def test_health_says_openai_is_not_configured_without_leaking_the_key(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _get_health(factory, openai_api_key="")

    assert body["openai_configured"] is False
    assert body["document_count"] == 0


@pytest.mark.asyncio
async def test_live_is_ok_without_a_database_or_redis() -> None:
    app = FastAPI()
    app.include_router(health_router)

    status_code, body = await _get_json(app, "/api/live")

    assert status_code == 200
    assert body == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_is_ok_when_the_database_and_redis_answer(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    status_code, body = await _get_json(_ready_app(factory, OkRedis()), "/api/ready")

    assert status_code == 200
    assert body == {"status": "ok", "database": "ok", "redis": "ok"}


@pytest.mark.asyncio
async def test_ready_is_unavailable_when_redis_was_never_connected(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    status_code, body = await _get_json(_ready_app(factory, None), "/api/ready")

    assert status_code == 503
    assert body == {"status": "unavailable", "database": "ok", "redis": "unavailable"}


@pytest.mark.asyncio
async def test_ready_is_unavailable_when_redis_is_missing_from_app_state(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    status_code, body = await _get_json(
        _ready_app(factory, None, set_redis_client=False), "/api/ready"
    )

    assert status_code == 503
    assert body["redis"] == "unavailable"


@pytest.mark.asyncio
async def test_ready_is_unavailable_when_redis_ping_fails(
    factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="atlas.health"):
        status_code, body = await _get_json(_ready_app(factory, FailingRedis()), "/api/ready")

    assert status_code == 503
    assert body == {"status": "unavailable", "database": "ok", "redis": "unavailable"}
    assert "Readiness Redis check failed" in caplog.text


@pytest.mark.asyncio
async def test_ready_is_unavailable_when_redis_ping_returns_false(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    status_code, body = await _get_json(_ready_app(factory, SilentRedis()), "/api/ready")

    assert status_code == 503
    assert body["redis"] == "unavailable"


@pytest.mark.asyncio
async def test_ready_is_unavailable_when_redis_has_no_ping(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    status_code, body = await _get_json(_ready_app(factory, NoPingRedis()), "/api/ready")

    assert status_code == 503
    assert body["redis"] == "unavailable"


@pytest.mark.asyncio
async def test_ready_is_unavailable_when_the_database_cannot_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="atlas.health"):
        status_code, body = await _get_json(
            _ready_app(LockedDatabaseFactory(), OkRedis()), "/api/ready"
        )

    assert status_code == 503
    assert body == {"status": "unavailable", "database": "unavailable", "redis": "ok"}
    assert "Readiness database check failed" in caplog.text


@pytest.mark.asyncio
async def test_ready_is_unavailable_when_the_session_factory_is_missing() -> None:
    status_code, body = await _get_json(_ready_app(None, OkRedis()), "/api/ready")

    assert status_code == 503
    assert body == {"status": "unavailable", "database": "unavailable", "redis": "ok"}
