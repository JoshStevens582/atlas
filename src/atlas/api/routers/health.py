from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import DocumentRepository
from atlas.schemas.chat import HealthOut

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    chunk_store: ChromaChunkStore = request.app.state.chunk_store
    async with session_factory() as session:
        document_count = await DocumentRepository(session).count_documents()
    return HealthOut(
        status="ok",
        openai_configured=bool(request.app.state.settings.openai_api_key),
        vector_store="chromadb",
        document_count=document_count,
        chunk_count=chunk_store.count(),
    )
