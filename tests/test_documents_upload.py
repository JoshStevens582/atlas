from collections.abc import AsyncIterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.documents import router as documents_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.schemas.chat import DocumentOut
from atlas.services.auth import seed_demo_users
from atlas.services.ingest import IngestService
from atlas.services.readers import DocumentReadError


@dataclass
class UploadHarness:
    client: AsyncClient
    ingest: AsyncMock
    upload_dir: Path


@pytest.fixture
async def upload_harness(tmp_path: Path) -> AsyncIterator[UploadHarness]:
    upload_dir = tmp_path / "uploads"
    settings = Settings(
        openai_api_key="sk-test",
        atlas_auth_secret="upload-test-secret",
        atlas_demo_users="alice:secret-a",
        upload_dir=str(upload_dir),
        max_upload_bytes=1024,
        rate_limit_enabled=False,
    )
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
    app.state.rate_limiter = None
    app.include_router(auth_router)
    app.include_router(documents_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield UploadHarness(client=http, ingest=ingest, upload_dir=upload_dir)
    await engine.dispose()


async def _auth_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret-a"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_upload_requires_login(upload_harness: UploadHarness) -> None:
    response = await upload_harness.client.post(
        "/api/documents/upload",
        files={"file": ("notes.md", b"hello", "text/markdown")},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type(upload_harness: UploadHarness) -> None:
    headers = await _auth_headers(upload_harness.client)
    response = await upload_harness.client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.docx", b"hello", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "md" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(upload_harness: UploadHarness) -> None:
    headers = await _auth_headers(upload_harness.client)
    response = await upload_harness.client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.md", b"", "text/markdown")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "The uploaded file is empty."


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(upload_harness: UploadHarness) -> None:
    headers = await _auth_headers(upload_harness.client)
    response = await upload_harness.client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.md", b"x" * 1025, "text/markdown")},
    )
    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_maps_document_read_error_to_400(
    upload_harness: UploadHarness,
) -> None:
    headers = await _auth_headers(upload_harness.client)
    upload_harness.ingest.ingest_path = AsyncMock(
        side_effect=DocumentReadError("The file is not valid UTF-8 text.")
    )

    response = await upload_harness.client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.txt", b"ok", "text/plain")},
    )
    assert response.status_code == 400
    assert "UTF-8" in response.json()["detail"]
    assert list(upload_harness.upload_dir.glob("*")) == []


@pytest.mark.asyncio
async def test_upload_accepts_valid_markdown(upload_harness: UploadHarness) -> None:
    headers = await _auth_headers(upload_harness.client)
    response = await upload_harness.client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.md", b"# Hello\n", "text/markdown")},
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["id"] == "doc-1"


def _blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_upload_blank_pdf_returns_400_when_reader_fails(
    upload_harness: UploadHarness,
) -> None:
    headers = await _auth_headers(upload_harness.client)
    upload_harness.ingest.ingest_path = AsyncMock(
        side_effect=DocumentReadError("The PDF had no extractable text.")
    )
    response = await upload_harness.client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("blank.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 400
    assert "extractable text" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_sync_fallback_cleans_up_on_unexpected_error(
    upload_harness: UploadHarness,
) -> None:
    """The no-Redis synchronous path used to leak the saved file and return a
    raw 500 on any exception other than DocumentReadError/IngestError (e.g. an
    OpenAI outage or a DB error during ingest). It must now clean up and
    return a clean 500, matching the Redis worker path's behaviour.
    """
    headers = await _auth_headers(upload_harness.client)
    upload_harness.ingest.ingest_path = AsyncMock(
        side_effect=RuntimeError("OpenAI embeddings API is unreachable.")
    )

    response = await upload_harness.client.post(
        "/api/documents/upload",
        headers=headers,
        files={"file": ("notes.md", b"# Hello\n", "text/markdown")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Ingest failed unexpectedly."
    # No orphaned file left behind on disk.
    assert list(upload_harness.upload_dir.glob("*")) == []
