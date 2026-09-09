from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI

from atlas.api.routers.chat import router as chat_router
from atlas.api.routers.documents import router as documents_router
from atlas.api.routers.health import router as health_router
from atlas.config import load_settings
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.rag import RagChatService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    Path("data").mkdir(exist_ok=True)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings)
    await init_database(engine)
    session_factory = create_session_factory(engine)
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key or None)
    embeddings = EmbeddingClient(openai_client, settings.openai_embedding_model)
    chunk_store = ChromaChunkStore(settings.chroma_path)
    ingest_service = IngestService(settings, session_factory, chunk_store, embeddings)
    rag_service = RagChatService(
        settings,
        openai_client,
        session_factory,
        chunk_store,
        embeddings,
    )

    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.chunk_store = chunk_store
    app.state.ingest_service = ingest_service
    app.state.rag_service = rag_service

    if settings.openai_api_key:
        await ingest_service.seed_sample_docs()

    yield
    await engine.dispose()


app = FastAPI(
    title="Atlas",
    description="Grounded RAG chat with citations and a retrieval inspector.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=load_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(documents_router)
