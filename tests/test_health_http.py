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


async def _get_health(
    factory: async_sessionmaker[AsyncSession], openai_api_key: str
) -> dict[str, object]:
    app = FastAPI()
    app.state.settings = Settings(openai_api_key=openai_api_key)
    app.state.session_factory = factory
    app.state.chunk_store = cast(ChromaChunkStore, StubChunkStore())
    app.include_router(health_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    body: dict[str, object] = response.json()
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
