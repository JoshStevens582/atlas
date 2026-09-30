"""CLI: wipe every document and upload, then index the handbook library again.

Run it with the API stopped (Chroma and SQLite are not meant for two writers):

    uv run python -m atlas.reset_library

On the server:

    docker compose stop api
    docker compose run --rm api python -m atlas.reset_library
    docker compose up -d api

Unlike the boot-time seed, this runs the retrieval self-test on every page, so
the Library shows a score for each one. It costs a few model calls per page.
"""

import asyncio
import logging
from pathlib import Path

from openai import AsyncOpenAI

from atlas.config import UNSET_OPENAI_API_KEY_PLACEHOLDER, load_settings
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.rag import RagChatService
from atlas.services.rerank import LlmReranker
from atlas.services.retrieval_selftest import LlmQuestionWriter, RetrievalSelfTest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


async def _run() -> None:
    settings = load_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set. Add it to .env before resetting.")

    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings)
    await init_database(engine)
    session_factory = create_session_factory(engine)
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key or UNSET_OPENAI_API_KEY_PLACEHOLDER
    )
    embeddings = EmbeddingClient(openai_client, settings.openai_embedding_model)
    chunk_store = ChromaChunkStore(settings.chroma_path)
    reranker = (
        LlmReranker(openai_client, settings.rerank_model) if settings.rerank_enabled else None
    )
    retriever = RagChatService(
        settings,
        openai_client,
        session_factory,
        chunk_store,
        embeddings,
        reranker=reranker,
    )
    retrieval_check = (
        RetrievalSelfTest(
            chunk_store,
            retriever,
            LlmQuestionWriter(openai_client, settings.retrieval_check_model),
            settings.retrieval_check_samples,
        )
        if settings.retrieval_check_enabled
        else None
    )
    ingest = IngestService(
        settings, session_factory, chunk_store, embeddings, retrieval_check=retrieval_check
    )
    try:
        documents = await ingest.reset_library()
    finally:
        await engine.dispose()

    for document in documents:
        check = document.retrieval_check
        score = f"self-test {check.hits}/{check.total}" if check else "self-test n/a"
        print(f"{document.title}: {document.chunk_count} chunk(s), {score}")
    print(f"Library reset: {len(documents)} documents.")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
