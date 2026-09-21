"""CLI worker: BRPOP ingest jobs and write Chroma + SQLite.

Run beside the API when ``ingest_worker_embedded`` is false:

    uv run python -m atlas.ingest_worker
"""

from __future__ import annotations

import asyncio
import logging
import signal

from openai import AsyncOpenAI

from atlas.config import load_settings
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.ingest_worker import process_next_ingest_job
from atlas.services.redis_client import connect_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("atlas.ingest_worker_cli")


async def _run() -> None:
    settings = load_settings()
    redis = await connect_redis(settings)
    if redis is None:
        raise SystemExit(
            "Redis is required for the ingest worker. "
            "Start Redis and set REDIS_URL, or enable ingest_queue."
        )

    engine = create_engine(settings)
    await init_database(engine)
    session_factory = create_session_factory(engine)
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key or None)
    embeddings = EmbeddingClient(openai_client, settings.openai_embedding_model)
    chunk_store = ChromaChunkStore(settings.chroma_path)
    ingest = IngestService(settings, session_factory, chunk_store, embeddings)
    queue = IngestQueue(redis, settings)

    stop = asyncio.Event()

    def _request_stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows: signal handlers in asyncio are limited.
            signal.signal(sig, lambda *_args: _request_stop())

    logger.info("ingest worker listening on %s", settings.ingest_queue_key)
    try:
        while not stop.is_set():
            handled = await process_next_ingest_job(queue, ingest, timeout_seconds=2)
            if not handled:
                await asyncio.sleep(0)
    finally:
        await redis.aclose()
        await engine.dispose()
        logger.info("ingest worker stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
