from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.documents import router as documents_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.schemas.chat import DocumentOut
from atlas.services.auth import seed_demo_users
from atlas.services.ingest import IngestError, IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.ingest_worker import process_next_ingest_job
from atlas.services.rate_limit import RateLimiter

type QueueHarness = tuple[AsyncClient, IngestQueue, AsyncMock, Path]


@pytest.fixture
async def queue_harness(tmp_path: Path) -> AsyncIterator[QueueHarness]:
    upload_dir = tmp_path / "uploads"
    settings = Settings(
        openai_api_key="sk-test",
        atlas_auth_secret="queue-test-secret",
        atlas_demo_users="alice:secret-a",
        upload_dir=str(upload_dir),
        max_upload_bytes=1024,
        ingest_queue_key="atlas:test:ingest",
        ingest_job_ttl_seconds=60,
        rate_limit_enabled=True,
        rate_limit_ask_per_minute=100,
        rate_limit_ask_per_day=100,
        rate_limit_ask_global_per_day=100,
        rate_limit_upload_per_minute=100,
        rate_limit_upload_per_day=100,
    )
    redis = FakeRedis(decode_responses=True)
    queue = IngestQueue(redis, settings)
    limiter = RateLimiter(redis, settings)
    ingest = AsyncMock(spec=IngestService)
    ingest.ingest_path = AsyncMock(
        return_value=DocumentOut(
            id="doc-queued",
            title="Notes",
            original_filename="notes.md",
            chunk_count=2,
            created_at="2026-09-21T00:00:00",
        )
    )

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await seed_demo_users(session_factory, settings)

    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.ingest_service = ingest
    app.state.ingest_queue = queue
    app.state.rate_limiter = limiter
    app.include_router(auth_router)
    app.include_router(documents_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http, queue, ingest, upload_dir
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
async def test_upload_enqueues_job_and_returns_202(queue_harness: QueueHarness) -> None:
    client, queue, ingest, _upload_dir = queue_harness
    headers = await _auth_headers(client)

    response = await client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.md", b"# Hello\n", "text/markdown")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["original_filename"] == "notes.md"
    ingest.ingest_path.assert_not_awaited()

    job = await queue.get_job(body["job_id"])
    assert job is not None
    assert job.status.value == "pending"


@pytest.mark.asyncio
async def test_worker_marks_job_done(queue_harness: QueueHarness) -> None:
    client, queue, ingest, upload_dir = queue_harness
    headers = await _auth_headers(client)
    response = await client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.md", b"# Hello\n", "text/markdown")},
    )
    job_id = response.json()["job_id"]

    handled = await process_next_ingest_job(queue, ingest, timeout_seconds=1)
    assert handled is True
    ingest.ingest_path.assert_awaited_once()

    status = await client.get(f"/api/documents/jobs/{job_id}", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["document_id"] == "doc-queued"
    assert body["chunk_count"] == 2
    assert list(upload_dir.glob("*.md"))  # file kept after success


@pytest.mark.asyncio
async def test_worker_marks_job_failed_and_deletes_file(queue_harness: QueueHarness) -> None:
    client, queue, ingest, upload_dir = queue_harness
    ingest.ingest_path = AsyncMock(side_effect=IngestError("The document produced no text chunks."))
    headers = await _auth_headers(client)
    response = await client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.md", b"# Hello\n", "text/markdown")},
    )
    job_id = response.json()["job_id"]

    handled = await process_next_ingest_job(queue, ingest, timeout_seconds=1)
    assert handled is True

    status = await client.get(f"/api/documents/jobs/{job_id}", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "failed"
    assert "no text chunks" in body["error"]
    assert list(upload_dir.glob("*")) == []
