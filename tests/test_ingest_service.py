import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import DocumentRepository
from atlas.schemas.chat import DocumentOut, RetrievalCheckOut
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestError, IngestService
from atlas.services.retrieval_selftest import RetrievalSelfTest

DEMO_TEXT = "# Demo Note\n\nRefunds are issued within 14 days.\n"


class RecordingChunkStore:
    """Remembers what ingest asked it to do; no Chroma involved."""

    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, list[str], list[list[float]]]] = []
        self.deleted_ids: list[str] = []
        self.reset_calls = 0

    def upsert_chunks(
        self,
        document_id: str,
        title: str,
        chunks: list[str],
        vectors: list[list[float]],
    ) -> None:
        self.upserts.append((document_id, title, chunks, vectors))

    def delete_document(self, document_id: str) -> None:
        self.deleted_ids.append(document_id)

    def reset(self) -> None:
        self.reset_calls += 1


class FakeEmbeddings:
    def __init__(self, *, drop_last: bool = False) -> None:
        self._drop_last = drop_last

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = [[float(len(text))] for text in texts]
        return vectors[:-1] if self._drop_last else vectors


class StubSelfTest:
    def __init__(
        self,
        result: RetrievalCheckOut | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.document_ids: list[str] = []

    async def run(self, document_id: str) -> RetrievalCheckOut | None:
        self.document_ids.append(document_id)
        if self._error is not None:
            raise self._error
        return self._result


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


def _service(
    factory: async_sessionmaker[AsyncSession],
    store: RecordingChunkStore,
    tmp_path: Path,
    *,
    with_library: bool = True,
    embeddings: FakeEmbeddings | None = None,
    retrieval_check: StubSelfTest | None = None,
) -> IngestService:
    library = tmp_path / "handbook"
    library.mkdir()
    if with_library:
        (library / "b-travel.md").write_text("Book flights early.\n", encoding="utf-8")
        (library / "a-leave.md").write_text(DEMO_TEXT, encoding="utf-8")
        (library / "notes.csv").write_text("not,a,document\n", encoding="utf-8")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    settings = Settings(library_dir=str(library), upload_dir=str(uploads))
    return IngestService(
        settings,
        factory,
        cast(ChromaChunkStore, store),
        cast(EmbeddingClient, embeddings or FakeEmbeddings()),
        retrieval_check=cast(RetrievalSelfTest | None, retrieval_check),
    )


async def _document_count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return await DocumentRepository(session).count_documents()


@pytest.mark.asyncio
async def test_ingest_text_stores_chunks_and_records_the_document(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()

    document = await _service(factory, store, tmp_path).ingest_text(
        DEMO_TEXT, title="Demo", original_filename="demo.md", document_id="doc-1"
    )

    assert document.id == "doc-1"
    assert document.title == "Demo"
    assert document.original_filename == "demo.md"
    assert document.chunk_count == len(store.upserts[0][2]) >= 1
    stored_id, stored_title, stored_chunks, stored_vectors = store.upserts[0]
    assert (stored_id, stored_title) == ("doc-1", "Demo")
    assert len(stored_vectors) == len(stored_chunks)
    assert await _document_count(factory) == 1


@pytest.mark.asyncio
async def test_ingest_text_stores_and_returns_the_self_test_score(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    selftest = StubSelfTest(RetrievalCheckOut(hits=5, total=6))

    document = await _service(
        factory, RecordingChunkStore(), tmp_path, retrieval_check=selftest
    ).ingest_text(DEMO_TEXT, title="Demo", original_filename="demo.md", document_id="doc-1")

    assert selftest.document_ids == ["doc-1"]
    assert document.retrieval_check == RetrievalCheckOut(hits=5, total=6)
    async with factory() as session:
        listed = await DocumentRepository(session).list_documents()
    assert DocumentOut.from_document(listed[0]).retrieval_check == RetrievalCheckOut(
        hits=5, total=6
    )


@pytest.mark.asyncio
async def test_ingest_text_leaves_the_score_empty_when_the_self_test_has_none(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    document = await _service(
        factory, RecordingChunkStore(), tmp_path, retrieval_check=StubSelfTest(None)
    ).ingest_text(DEMO_TEXT, title="Demo", original_filename="demo.md")

    assert document.retrieval_check is None


@pytest.mark.asyncio
async def test_ingest_text_still_succeeds_when_the_self_test_crashes(
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    selftest = StubSelfTest(error=RuntimeError("chroma exploded"))

    with caplog.at_level(logging.ERROR, logger="atlas.ingest"):
        document = await _service(
            factory, RecordingChunkStore(), tmp_path, retrieval_check=selftest
        ).ingest_text(DEMO_TEXT, title="Demo", original_filename="demo.md")

    assert document.retrieval_check is None
    assert await _document_count(factory) == 1
    assert "retrieval self-test crashed" in caplog.text


@pytest.mark.asyncio
async def test_set_retrieval_check_ignores_an_unknown_document(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        assert await DocumentRepository(session).set_retrieval_check("nope", 1, 2) is None


@pytest.mark.asyncio
async def test_ingest_text_makes_up_an_id_when_none_is_given(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()

    document = await _service(factory, store, tmp_path).ingest_text(
        DEMO_TEXT, title="Demo", original_filename="demo.md"
    )

    assert document.id
    assert store.upserts[0][0] == document.id


@pytest.mark.asyncio
async def test_ingest_text_rejects_text_that_makes_no_chunks(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()

    with pytest.raises(IngestError, match="no text chunks"):
        await _service(factory, store, tmp_path).ingest_text(
            "", title="Empty", original_filename="empty.md"
        )

    assert store.upserts == []
    assert await _document_count(factory) == 0


@pytest.mark.asyncio
async def test_ingest_text_writes_nothing_when_embedding_count_is_wrong(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    service = _service(factory, store, tmp_path, embeddings=FakeEmbeddings(drop_last=True))

    with pytest.raises(IngestError, match="did not match"):
        await service.ingest_text(DEMO_TEXT, title="Demo", original_filename="demo.md")

    assert store.upserts == []
    assert await _document_count(factory) == 0


@pytest.mark.asyncio
async def test_ingest_path_reads_the_file_and_keeps_its_name(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    handbook = tmp_path / "handbook.md"
    handbook.write_text(DEMO_TEXT, encoding="utf-8")

    document = await _service(factory, store, tmp_path).ingest_path(handbook, title="Handbook")

    assert document.title == "Handbook"
    assert document.original_filename == "handbook.md"
    assert "Refunds" in store.upserts[0][2][0]


@pytest.mark.asyncio
async def test_seed_library_indexes_every_page_once_named_for_its_heading(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    selftest = StubSelfTest(RetrievalCheckOut(hits=1, total=1))
    service = _service(factory, store, tmp_path, retrieval_check=selftest)

    first = await service.seed_library()
    second = await service.seed_library()

    assert [(doc.original_filename, doc.title) for doc in first] == [
        ("a-leave.md", "Demo Note"),
        ("b-travel.md", "B Travel"),
    ]
    assert second == []
    assert len(store.upserts) == 2
    assert selftest.document_ids == []
    assert all(doc.retrieval_check is None for doc in first)


@pytest.mark.asyncio
async def test_seed_library_does_nothing_when_the_library_folder_is_empty_or_missing(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    empty = _service(factory, store, tmp_path, with_library=False)

    assert await empty.seed_library() == []

    (tmp_path / "handbook").rmdir()
    assert await empty.seed_library() == []
    assert store.upserts == []


@pytest.mark.asyncio
async def test_reset_library_replaces_everything_and_scores_every_page(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    selftest = StubSelfTest(RetrievalCheckOut(hits=2, total=3))
    service = _service(factory, store, tmp_path, retrieval_check=selftest)
    await service.ingest_text("Old upload", title="Old", original_filename="old.txt")
    upload_dir = tmp_path / "uploads"
    (upload_dir / "old.txt").write_text("Old upload", encoding="utf-8")
    (upload_dir / "keep-folder").mkdir()

    documents = await service.reset_library()

    assert [doc.original_filename for doc in documents] == ["a-leave.md", "b-travel.md"]
    assert await _document_count(factory) == 2
    assert store.reset_calls == 1
    assert len(selftest.document_ids) == 3  # the old upload was scored too, before the reset
    assert [doc.retrieval_check for doc in documents] == [RetrievalCheckOut(hits=2, total=3)] * 2
    assert not (upload_dir / "old.txt").exists()
    assert (upload_dir / "keep-folder").is_dir()


@pytest.mark.asyncio
async def test_reset_library_works_when_the_upload_folder_does_not_exist_yet(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    service = _service(factory, store, tmp_path)
    (tmp_path / "uploads").rmdir()

    documents = await service.reset_library()

    assert len(documents) == 2
    assert await _document_count(factory) == 2


@pytest.mark.asyncio
async def test_reset_library_refuses_to_wipe_when_the_library_is_missing(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    service = _service(factory, store, tmp_path, with_library=False)
    await service.ingest_text("Keep me", title="Keep", original_filename="keep.txt")

    with pytest.raises(IngestError, match="No library documents"):
        await service.reset_library()

    assert store.reset_calls == 0
    assert await _document_count(factory) == 1


@pytest.mark.asyncio
async def test_reset_and_ingest_paths_wipes_then_indexes_each_file(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    service = _service(factory, store, tmp_path)
    await service.ingest_text("Old upload", title="Old", original_filename="old.txt")
    first = tmp_path / "one.md"
    second = tmp_path / "two.md"
    first.write_text(DEMO_TEXT, encoding="utf-8")
    second.write_text("Shipping takes 3 days.", encoding="utf-8")

    indexed = await service.reset_and_ingest_paths([first, second])

    assert [document.original_filename for document in indexed] == ["one.md", "two.md"]
    assert store.reset_calls == 1
    assert await _document_count(factory) == 2


@pytest.mark.asyncio
async def test_reset_and_ingest_paths_refuses_an_empty_list_without_wiping(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    service = _service(factory, store, tmp_path)
    await service.ingest_text("Keep me", title="Keep", original_filename="keep.txt")

    with pytest.raises(IngestError, match="No documents"):
        await service.reset_and_ingest_paths([])

    assert store.reset_calls == 0
    assert await _document_count(factory) == 1


@pytest.mark.asyncio
async def test_delete_document_removes_row_and_chunks(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()
    service = _service(factory, store, tmp_path)
    document = await service.ingest_text("Bye", title="Bye", original_filename="bye.txt")

    deleted = await service.delete_document(document.id)

    assert deleted is True
    assert store.deleted_ids == [document.id]
    assert await _document_count(factory) == 0


@pytest.mark.asyncio
async def test_delete_document_leaves_the_vector_store_alone_for_an_unknown_id(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    store = RecordingChunkStore()

    deleted = await _service(factory, store, tmp_path).delete_document("does-not-exist")

    assert deleted is False
    assert store.deleted_ids == []
