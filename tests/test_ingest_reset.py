from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import DocumentRepository
from atlas.services.ingest import IngestError, IngestService


class StubEmbeddingClient:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_reset_and_ingest_paths_indexes_files(tmp_path: Path) -> None:
    first = tmp_path / "alpha.md"
    second = tmp_path / "beta.md"
    first.write_text("Alpha document about Northstar.", encoding="utf-8")
    second.write_text("Beta document about refunds.", encoding="utf-8")
    db_path = tmp_path / "atlas.db"
    settings = Settings(
        openai_api_key="test-key",
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        chroma_path=str(tmp_path / "chroma"),
        upload_dir=str(tmp_path / "uploads"),
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    ingest = IngestService(
        settings,
        session_factory,
        ChromaChunkStore(settings.chroma_path),
        StubEmbeddingClient(),  # type: ignore[arg-type]
    )
    indexed = await ingest.reset_and_ingest_paths([first, second])
    assert [item.original_filename for item in indexed] == ["alpha.md", "beta.md"]
    async with session_factory() as session:
        assert await DocumentRepository(session).count_documents() == 2
    replaced = tmp_path / "gamma.md"
    replaced.write_text("Only gamma remains.", encoding="utf-8")
    second_run = await ingest.reset_and_ingest_paths([replaced])
    assert len(second_run) == 1
    async with session_factory() as session:
        assert await DocumentRepository(session).count_documents() == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_reset_and_ingest_paths_rejects_empty_list(tmp_path: Path) -> None:
    db_path = tmp_path / "atlas.db"
    settings = Settings(
        openai_api_key="test-key",
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        chroma_path=str(tmp_path / "chroma"),
        upload_dir=str(tmp_path / "uploads"),
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    ingest = IngestService(
        settings,
        session_factory,
        ChromaChunkStore(settings.chroma_path),
        StubEmbeddingClient(),  # type: ignore[arg-type]
    )
    with pytest.raises(IngestError, match="No documents"):
        await ingest.reset_and_ingest_paths([])
    await engine.dispose()
