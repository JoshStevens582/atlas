import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI

from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.chat import router as chat_router
from atlas.api.routers.documents import router as documents_router
from atlas.api.routers.health import router as health_router
from atlas.config import (
    UNSET_OPENAI_API_KEY_PLACEHOLDER,
    load_settings,
    require_secure_auth_secret,
)
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.answer_cache import AnswerCache
from atlas.services.answer_check import LlmAnswerChecker
from atlas.services.auth import seed_demo_users
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.ingest_worker import run_worker_loop
from atlas.services.rag import RagChatService
from atlas.services.rate_limit import RateLimiter
from atlas.services.redis_client import connect_redis
from atlas.services.rerank import LlmReranker
from atlas.services.retrieval_selftest import LlmQuestionWriter, RetrievalSelfTest


def _configure_logging() -> None:
    """Ensure Ask traces show in the API terminal (uvicorn may already own root)."""
    ask_logger = logging.getLogger("atlas.ask")
    ask_logger.setLevel(logging.INFO)
    if ask_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    ask_logger.addHandler(handler)
    ask_logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    settings = load_settings()
    require_secure_auth_secret(settings)
    Path("data").mkdir(exist_ok=True)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings)
    await init_database(engine)
    session_factory = create_session_factory(engine)
    if settings.atlas_auth_secret:
        await seed_demo_users(session_factory, settings)
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key or UNSET_OPENAI_API_KEY_PLACEHOLDER
    )
    embeddings = EmbeddingClient(openai_client, settings.openai_embedding_model)
    chunk_store = ChromaChunkStore(settings.chroma_path)

    redis_client = await connect_redis(settings)
    answer_cache = (
        AnswerCache(redis_client, settings)
        if redis_client is not None and settings.answer_cache_enabled
        else None
    )
    reranker = (
        LlmReranker(openai_client, settings.rerank_model)
        if settings.rerank_enabled
        else None
    )
    answer_checker = (
        LlmAnswerChecker(openai_client, settings.answer_check_model)
        if settings.answer_check_enabled
        else None
    )
    rag_service = RagChatService(
        settings,
        openai_client,
        session_factory,
        chunk_store,
        embeddings,
        answer_cache=answer_cache,
        reranker=reranker,
        answer_checker=answer_checker,
    )
    retrieval_check = (
        RetrievalSelfTest(
            chunk_store,
            rag_service,
            LlmQuestionWriter(openai_client, settings.retrieval_check_model),
            settings.retrieval_check_samples,
        )
        if settings.retrieval_check_enabled
        else None
    )
    ingest_service = IngestService(
        settings, session_factory, chunk_store, embeddings, retrieval_check=retrieval_check
    )
    ingest_queue = (
        IngestQueue(redis_client, settings)
        if redis_client is not None and settings.ingest_queue_enabled
        else None
    )
    rate_limiter = (
        RateLimiter(redis_client, settings)
        if redis_client is not None and settings.rate_limit_enabled
        else None
    )
    worker_task: asyncio.Task[None] | None = None
    worker_stop = asyncio.Event()

    # Same process as FastAPI: pull Redis notes and run ingest (not Redis itself).
    # run_worker_loop backs off and retries on Redis errors instead of dying.
    async def _embedded_worker() -> None:
        assert ingest_queue is not None
        await run_worker_loop(
            ingest_queue,
            ingest_service,
            stop=worker_stop,
            poll_timeout_seconds=1,
        )

    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.chunk_store = chunk_store
    app.state.ingest_service = ingest_service
    app.state.rag_service = rag_service
    app.state.ingest_queue = ingest_queue
    app.state.rate_limiter = rate_limiter
    app.state.redis_client = redis_client

    if (
        ingest_queue is not None
        and settings.ingest_worker_embedded
        and settings.openai_api_key
    ):
        worker_task = asyncio.create_task(_embedded_worker(), name="atlas-ingest-worker")

    if settings.openai_api_key:
        await ingest_service.seed_library()

    yield

    worker_stop.set()
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    if redis_client is not None:
        await redis_client.aclose()
    await engine.dispose()


# Only what the React app actually sends. Anything else is refused at the preflight.
CORS_ALLOWED_METHODS = ["GET", "POST", "DELETE"]
CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type"]

app = FastAPI(
    title="Atlas",
    description="Grounded RAG chat with citations and a Sources used panel.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=load_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=CORS_ALLOWED_METHODS,
    allow_headers=CORS_ALLOWED_HEADERS,
)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(documents_router)
