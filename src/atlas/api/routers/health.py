import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import DocumentRepository
from atlas.schemas.chat import HealthOut, LiveOut, ReadyOut

router = APIRouter(prefix="/api", tags=["health"])
logger = logging.getLogger("atlas.health")

_READY_CHECK_SECONDS = 2.0
DependencyStatus = Literal["ok", "unavailable"]


@router.get("/live", response_model=LiveOut)
async def live() -> LiveOut:
    """Liveness: the process is running. No Redis or database I/O."""
    return LiveOut(status="ok")


@router.get("/ready", response_model=ReadyOut)
async def ready(request: Request, response: Response) -> ReadyOut:
    """Readiness: Redis and the database answer. 503 if either does not."""
    session_factory = getattr(request.app.state, "session_factory", None)
    redis_client = getattr(request.app.state, "redis_client", None)
    database = (
        "unavailable"
        if session_factory is None
        else await _database_status(session_factory)
    )
    redis = await _redis_status(redis_client)
    payload = ReadyOut(
        status="ok" if database == "ok" and redis == "ok" else "unavailable",
        database=database,
        redis=redis,
    )
    if payload.status != "ok":
        response.status_code = 503
    return payload


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


async def _database_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> DependencyStatus:
    try:
        async with session_factory() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")),
                timeout=_READY_CHECK_SECONDS,
            )
    except Exception as exc:
        logger.warning("Readiness database check failed (%s).", exc)
        return "unavailable"
    return "ok"


async def _redis_status(client: object | None) -> DependencyStatus:
    if client is None:
        return "unavailable"
    ping = getattr(client, "ping", None)
    if ping is None:
        return "unavailable"
    try:
        replied = await asyncio.wait_for(ping(), timeout=_READY_CHECK_SECONDS)
    except Exception as exc:
        logger.warning("Readiness Redis check failed (%s).", exc)
        return "unavailable"
    if not replied:
        return "unavailable"
    return "ok"
