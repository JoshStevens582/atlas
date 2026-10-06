from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.documents import router as documents_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.sql_repo import DocumentRepository
from atlas.services.auth import seed_demo_users
from atlas.services.ingest import IngestService
from atlas.services.ingest_queue import IngestQueue


class InMemoryRedis:
    """Just enough of Redis for IngestQueue.enqueue / get_job."""

    def __init__(self, *, fail_on_push: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.pushed: list[str] = []
        self._fail_on_push = fail_on_push

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def lpush(self, _key: str, value: str) -> None:
        if self._fail_on_push:
            raise ConnectionError("redis went away")
        self.pushed.append(value)


@dataclass
class Harness:
    client: AsyncClient
    ingest: AsyncMock
    session_factory: async_sessionmaker[AsyncSession]
    upload_dir: Path
    headers: dict[str, str]
    redis: InMemoryRedis | None


@asynccontextmanager
async def _running_app(
    tmp_path: Path,
    *,
    openai_api_key: str = "sk-test",
    redis: InMemoryRedis | None = None,
) -> AsyncIterator[Harness]:
    settings = Settings(
        openai_api_key=openai_api_key,
        atlas_auth_secret="documents-router-test-key-long-enough",
        atlas_demo_users="alice:secret-a",
        upload_dir=str(tmp_path / "uploads"),
        rate_limit_enabled=False,
        login_lockout_enabled=False,
    )
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await seed_demo_users(factory, settings)

    ingest = AsyncMock(spec=IngestService)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.ingest_service = ingest
    app.state.ingest_queue = IngestQueue(cast(Redis, redis), settings) if redis else None
    app.include_router(auth_router)
    app.include_router(documents_router)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login", json={"username": "alice", "password": "secret-a"}
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            yield Harness(client, ingest, factory, tmp_path / "uploads", headers, redis)
    finally:
        await engine.dispose()


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async with _running_app(tmp_path) as running:
        yield running


@pytest.fixture
async def queued_harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async with _running_app(tmp_path, redis=InMemoryRedis()) as running:
        yield running


@pytest.mark.asyncio
async def test_upload_says_503_and_saves_nothing_when_no_openai_key(tmp_path: Path) -> None:
    async with _running_app(tmp_path, openai_api_key="") as running:
        response = await running.client.post(
            "/api/documents/upload",
            headers=running.headers,
            files={"file": ("notes.md", b"hello", "text/markdown")},
        )

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
    assert not running.upload_dir.exists()
    running.ingest.ingest_path.assert_not_called()


@pytest.mark.asyncio
async def test_upload_with_redis_returns_202_and_a_job_you_can_look_up(
    queued_harness: Harness,
) -> None:
    upload = await queued_harness.client.post(
        "/api/documents/upload",
        headers=queued_harness.headers,
        files={"file": ("handbook.md", b"Refunds take 14 days.", "text/markdown")},
    )

    assert upload.status_code == 202
    job = upload.json()
    assert job["status"] == "pending"
    assert job["original_filename"] == "handbook.md"
    assert queued_harness.redis is not None
    assert len(queued_harness.redis.pushed) == 1
    assert len(list(queued_harness.upload_dir.iterdir())) == 1
    queued_harness.ingest.ingest_path.assert_not_called()

    lookup = await queued_harness.client.get(
        f"/api/documents/jobs/{job['job_id']}", headers=queued_harness.headers
    )
    assert lookup.status_code == 200
    assert lookup.json()["job_id"] == job["job_id"]


@pytest.mark.asyncio
async def test_upload_removes_the_saved_file_when_the_queue_write_fails(tmp_path: Path) -> None:
    async with _running_app(tmp_path, redis=InMemoryRedis(fail_on_push=True)) as running:
        response = await running.client.post(
            "/api/documents/upload",
            headers=running.headers,
            files={"file": ("handbook.md", b"Refunds take 14 days.", "text/markdown")},
        )

    assert response.status_code == 503
    assert list(running.upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_unknown_job_is_404(queued_harness: Harness) -> None:
    response = await queued_harness.client.get(
        "/api/documents/jobs/nope", headers=queued_harness.headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_job_lookup_says_503_when_there_is_no_queue(harness: Harness) -> None:
    response = await harness.client.get("/api/documents/jobs/anything", headers=harness.headers)

    assert response.status_code == 503
    assert "Redis" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_documents_returns_what_is_in_the_library(harness: Harness) -> None:
    async with harness.session_factory() as session:
        await DocumentRepository(session).create_document(
            title="Demo Note", original_filename="demo.md", chunk_count=4
        )

    response = await harness.client.get("/api/documents", headers=harness.headers)

    assert response.status_code == 200
    assert [(item["title"], item["chunk_count"]) for item in response.json()] == [("Demo Note", 4)]


@pytest.mark.asyncio
async def test_list_documents_requires_login(harness: Harness) -> None:
    response = await harness.client.get("/api/documents")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_document_reports_deleted(harness: Harness) -> None:
    harness.ingest.delete_document.return_value = True

    response = await harness.client.delete("/api/documents/doc-1", headers=harness.headers)

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    harness.ingest.delete_document.assert_awaited_once_with("doc-1")


@pytest.mark.asyncio
async def test_delete_unknown_document_is_404(harness: Harness) -> None:
    harness.ingest.delete_document.return_value = False

    response = await harness.client.delete("/api/documents/nope", headers=harness.headers)

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_name", "method", "path", "message"),
    [
        ("ingest_service", "DELETE", "/api/documents/x", "Ingest service is not configured"),
        ("session_factory", "GET", "/api/documents", "session factory is not configured"),
        ("ingest_queue", "GET", "/api/documents/jobs/x", "queue is misconfigured"),
    ],
)
async def test_routes_fail_loudly_when_a_dependency_was_wired_wrong(
    state_name: str, method: str, path: str, message: str
) -> None:
    app = FastAPI()
    app.state.settings = Settings(atlas_auth_secret="")
    app.state.ingest_service = object()
    app.state.session_factory = object()
    app.state.ingest_queue = object()
    setattr(app.state, state_name, object())
    app.include_router(documents_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match=message):
            await client.request(method, path)
