from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.ingest import IngestService
from atlas.services.rag import RagChatService


class StubEmbeddingClient:
    """Same vector for every text so BM25 is what separates keyword hits."""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_retrieve_hybrid_keeps_keyword_chunk_vectors_would_tie(
    tmp_path: Path,
) -> None:
    handbook = tmp_path / "handbook.md"
    handbook.write_text(
        "The project codename is Northstar.\n\n"
        "Warranty serial ZX441NORTH is a SKU, not the project name.",
        encoding="utf-8",
    )
    settings = Settings(
        openai_api_key="test-key",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'atlas.db').as_posix()}",
        chroma_path=str(tmp_path / "chroma"),
        upload_dir=str(tmp_path / "uploads"),
        retrieve_k=2,
        hybrid_search_enabled=True,
        chunk_size=80,
        chunk_overlap=0,
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    store = ChromaChunkStore(settings.chroma_path)
    embeddings = StubEmbeddingClient()
    ingest = IngestService(
        settings,
        factory,
        store,
        embeddings,  # type: ignore[arg-type]
    )
    await ingest.ingest_path(handbook)
    rag = RagChatService(
        settings,
        None,  # type: ignore[arg-type]
        factory,
        store,
        embeddings,  # type: ignore[arg-type]
    )

    hits = await rag.retrieve("ZX441NORTH")
    await engine.dispose()

    assert hits
    assert any("ZX441NORTH" in chunk.text for chunk in hits)
    assert any(chunk.match in {"lexical", "both"} for chunk in hits)


@pytest.mark.asyncio
async def test_retrieve_vector_only_when_hybrid_disabled(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("The project codename is Northstar.", encoding="utf-8")
    settings = Settings(
        openai_api_key="test-key",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'atlas.db').as_posix()}",
        chroma_path=str(tmp_path / "chroma"),
        upload_dir=str(tmp_path / "uploads"),
        hybrid_search_enabled=False,
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    store = ChromaChunkStore(settings.chroma_path)
    embeddings = StubEmbeddingClient()
    ingest = IngestService(
        settings,
        factory,
        store,
        embeddings,  # type: ignore[arg-type]
    )
    await ingest.ingest_path(note)
    rag = RagChatService(
        settings,
        None,  # type: ignore[arg-type]
        factory,
        store,
        embeddings,  # type: ignore[arg-type]
    )

    hits = await rag.retrieve("What is the project codename?")
    await engine.dispose()

    assert hits
    assert all(chunk.match == "vector" for chunk in hits)
