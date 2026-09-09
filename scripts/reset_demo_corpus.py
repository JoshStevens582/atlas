"""Wipe duplicate Demo Notes and index sample_docs/00-demo-note.md once."""

import asyncio
from pathlib import Path

from openai import AsyncOpenAI

from atlas.config import load_settings
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService


async def main() -> None:
    settings = load_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set. Add it to .env before resetting.")

    Path("data").mkdir(exist_ok=True)
    engine = create_engine(settings)
    await init_database(engine)
    session_factory = create_session_factory(engine)
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    ingest = IngestService(
        settings,
        session_factory,
        ChromaChunkStore(settings.chroma_path),
        EmbeddingClient(openai_client, settings.openai_embedding_model),
    )
    document = await ingest.reset_corpus_to_demo_note()
    await engine.dispose()
    print(
        f"Corpus reset: {document.title} "
        f"({document.chunk_count} chunk(s), id={document.id})"
    )


if __name__ == "__main__":
    asyncio.run(main())
