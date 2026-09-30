import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import DocumentRepository
from atlas.schemas.chat import DocumentOut, RetrievalCheckOut
from atlas.services.chunking import chunk_document
from atlas.services.embeddings import EmbeddingClient
from atlas.services.readers import (
    SUPPORTED_SUFFIXES,
    extract_text,
    title_from_path,
    title_from_text,
)
from atlas.services.retrieval_selftest import RetrievalSelfTest

logger = logging.getLogger("atlas.ingest")


class IngestError(ValueError):
    """Raised when a document cannot be indexed."""


class IngestService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        chunk_store: ChromaChunkStore,
        embeddings: EmbeddingClient,
        retrieval_check: RetrievalSelfTest | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._chunk_store = chunk_store
        self._embeddings = embeddings
        self._retrieval_check = retrieval_check

    async def ingest_path(self, path: Path, title: str | None = None) -> DocumentOut:
        text = extract_text(path)
        return await self.ingest_text(
            text=text,
            title=title or title_from_path(path),
            original_filename=path.name,
        )

    async def ingest_text(
        self,
        text: str,
        title: str,
        original_filename: str,
        document_id: str | None = None,
        run_retrieval_check: bool = True,
    ) -> DocumentOut:
        chunks = chunk_document(
            text,
            self._settings.chunk_size,
            self._settings.chunk_overlap,
        )
        if not chunks:
            raise IngestError("The document produced no text chunks.")

        vectors = await self._embeddings.embed_texts(chunks)
        if len(vectors) != len(chunks):
            raise IngestError("Embedding count did not match chunk count.")

        doc_id = document_id or str(uuid4())
        await asyncio.to_thread(
            self._chunk_store.upsert_chunks,
            doc_id,
            title,
            chunks,
            vectors,
        )

        async with self._session_factory() as session:
            repo = DocumentRepository(session)
            document = await repo.create_document(
                title=title,
                original_filename=original_filename,
                chunk_count=len(chunks),
                document_id=doc_id,
            )
            check = await self._run_retrieval_check(doc_id) if run_retrieval_check else None
            if check is not None:
                scored = await repo.set_retrieval_check(doc_id, check.hits, check.total)
                document = scored or document
            return DocumentOut.from_document(document)

    async def _run_retrieval_check(self, document_id: str) -> RetrievalCheckOut | None:
        """The self-test is a courtesy. The document is already indexed, so a
        failure here is logged and the upload still succeeds with no score."""
        if self._retrieval_check is None:
            return None
        try:
            return await self._retrieval_check.run(document_id)
        except Exception:
            logger.exception("retrieval self-test crashed document_id=%s", document_id)
            return None

    def _library_paths(self) -> list[Path]:
        library_dir = Path(self._settings.library_dir)
        if not library_dir.is_dir():
            return []
        return sorted(
            path
            for path in library_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )

    async def _ingest_library_file(
        self, path: Path, *, run_retrieval_check: bool
    ) -> DocumentOut:
        """Library pages are named for their first heading, not their file name."""
        text = extract_text(path)
        return await self.ingest_text(
            text=text,
            title=title_from_text(text, title_from_path(path)),
            original_filename=path.name,
            run_retrieval_check=run_retrieval_check,
        )

    async def seed_library(self) -> list[DocumentOut]:
        """Index the handbook library once, on an empty database.

        The self-test is skipped here so a fresh boot is not held up by
        one extra round of model calls per page. ``reset_library`` runs it.
        """
        async with self._session_factory() as session:
            existing = await DocumentRepository(session).count_documents()
        if existing > 0:
            return []
        return [
            await self._ingest_library_file(path, run_retrieval_check=False)
            for path in self._library_paths()
        ]

    async def reset_library(self) -> list[DocumentOut]:
        """Wipe every document and upload, then index the handbook library again."""
        paths = self._library_paths()
        if not paths:
            raise IngestError(f"No library documents found in {self._settings.library_dir}.")
        async with self._session_factory() as session:
            await DocumentRepository(session).delete_all_documents()
        await asyncio.to_thread(self._chunk_store.reset)
        upload_dir = Path(self._settings.upload_dir)
        if upload_dir.exists():
            for path in upload_dir.iterdir():
                if path.is_file():
                    path.unlink()
        return [
            await self._ingest_library_file(path, run_retrieval_check=True) for path in paths
        ]

    async def reset_and_ingest_paths(self, paths: Sequence[Path]) -> list[DocumentOut]:
        """Wipe the index and ingest the given files. Used by the eval runner.

        Files are titled and indexed the way the library is, so the eval scores
        what the Library serves. The self-test is skipped: the eval is the test.
        """
        if not paths:
            raise IngestError("No documents were provided to index.")
        async with self._session_factory() as session:
            await DocumentRepository(session).delete_all_documents()
        await asyncio.to_thread(self._chunk_store.reset)
        indexed: list[DocumentOut] = []
        for path in paths:
            indexed.append(await self._ingest_library_file(path, run_retrieval_check=False))
        return indexed

    async def delete_document(self, document_id: str) -> bool:
        async with self._session_factory() as session:
            deleted = await DocumentRepository(session).delete_document(document_id)
        if deleted:
            await asyncio.to_thread(self._chunk_store.delete_document, document_id)
        return deleted
