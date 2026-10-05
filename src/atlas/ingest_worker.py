"""CLI worker: read Redis notes and run ingest (optional separate process).

Redis only stores tickets. This process chunks, calls the embedding model,
and writes Chroma + SQLite. Default Atlas runs the same loop inside FastAPI.

    uv run python -m atlas.ingest_worker
"""

from __future__ import annotations

import asyncio
import logging
import signal

from openai import AsyncOpenAI

from atlas.config import UNSET_OPENAI_API_KEY_PLACEHOLDER, load_settings
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.ingest_worker import run_worker_loop
from atlas.services.logging_setup import configure_logging, shutdown_logging
from atlas.services.redis_client import RedisRequiredError, connect_redis

logger = logging.getLogger("atlas.ingest_worker_cli")


async def _run() -> None:
    settings = load_settings()
    configure_logging(settings)
    try:
        redis = await connect_redis(settings)
    except RedisRequiredError as exc:
        raise SystemExit(str(exc)) from exc
    if redis is None:
        raise SystemExit(
            "Redis is required for the ingest worker. Start Redis and set REDIS_URL."
        )

    engine = create_engine(settings)
    await init_database(engine)
    session_factory = create_session_factory(engine)
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key or UNSET_OPENAI_API_KEY_PLACEHOLDER
    )
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
        # Backs off and retries on Redis errors instead of crash-looping.
        await run_worker_loop(queue, ingest, stop=stop, poll_timeout_seconds=2)
    finally:
        logger.info("ingest worker stopped")
        await redis.aclose()
        await engine.dispose()
        shutdown_logging()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
