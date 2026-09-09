import asyncio
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import DocumentRepository
from atlas.schemas.chat import DocumentOut
from atlas.services.chunking import chunk_document
from atlas.services.embeddings import EmbeddingClient
from atlas.services.readers import extract_text, title_from_path


class IngestError(ValueError):
    """Raised when a document cannot be indexed."""


class IngestService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        chunk_store: ChromaChunkStore,
        embeddings: EmbeddingClient,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._chunk_store = chunk_store
        self._embeddings = embeddings

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
            return DocumentOut(
                id=document.id,
                title=document.title,
                original_filename=document.original_filename,
                chunk_count=document.chunk_count,
                created_at=document.created_at.isoformat(),
            )

    def _demo_note_path(self) -> Path:
        return Path(self._settings.sample_docs_dir) / "00-demo-note.md"

    async def seed_sample_docs(self) -> list[DocumentOut]:
        """Index only the current Demo Note so boot does not reload old uploads."""
        async with self._session_factory() as session:
            existing = await DocumentRepository(session).count_documents()
        if existing > 0:
            return []
        demo_path = self._demo_note_path()
        if not demo_path.exists():
            return []
        return [await self.ingest_path(demo_path)]

    async def reset_corpus_to_demo_note(self) -> DocumentOut:
        """Wipe duplicate uploads/index rows and ingest the current Demo Note once."""
        async with self._session_factory() as session:
            await DocumentRepository(session).delete_all_documents()
        await asyncio.to_thread(self._chunk_store.reset)
        upload_dir = Path(self._settings.upload_dir)
        if upload_dir.exists():
            for path in upload_dir.iterdir():
                if path.is_file():
                    path.unlink()
        demo_path = self._demo_note_path()
        if not demo_path.exists():
            raise IngestError("sample_docs/00-demo-note.md is missing.")
        return await self.ingest_path(demo_path)

    async def delete_document(self, document_id: str) -> bool:
        async with self._session_factory() as session:
            deleted = await DocumentRepository(session).delete_document(document_id)
        if deleted:
            await asyncio.to_thread(self._chunk_store.delete_document, document_id)
        return deleted
