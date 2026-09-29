import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.chat import router as chat_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import ThreadAccessDeniedError, ThreadNotFoundError
from atlas.services.auth import seed_demo_users
from atlas.services.embeddings import EmbeddingClient
from atlas.services.rag import RagChatService


class ScriptedRag(RagChatService):
    """A RagChatService whose run_ask replays events, then optionally fails."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.events: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.calls: list[tuple[str, str | None, str]] = []

    async def run_ask(
        self, message: str, thread_id: str | None, owner_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((message, thread_id, owner_id))
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error


@dataclass
class Harness:
    client: AsyncClient
    rag: ScriptedRag
    headers: dict[str, str] = field(default_factory=dict)


async def _build(openai_api_key: str) -> tuple[AsyncClient, ScriptedRag, Any]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        openai_api_key=openai_api_key,
        atlas_auth_secret="http-test-secret-that-is-long-enough",
        atlas_demo_users="alice:secret-a",
        rate_limit_enabled=False,
    )
    await seed_demo_users(factory, settings)
    rag = ScriptedRag(
        settings,
        AsyncOpenAI(api_key="sk-test"),
        factory,
        cast(ChromaChunkStore, object()),
        cast(EmbeddingClient, object()),
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.rag_service = rag
    app.include_router(auth_router)
    app.include_router(chat_router)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, rag, engine


async def _login_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret-a"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    client, rag, engine = await _build(openai_api_key="sk-configured")
    async with client:
        yield Harness(client, rag, await _login_headers(client))
    await engine.dispose()


def _events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.split("\n\n")
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_stream_sends_each_event_as_a_data_line_for_the_signed_in_user(
    harness: Harness,
) -> None:
    harness.rag.events = [
        {"type": "token", "delta": "Hello"},
        {"type": "done", "thread_id": "t-1"},
    ]

    response = await harness.client.post(
        "/api/chat/stream",
        headers=harness.headers,
        json={"message": "hi", "thread_id": "t-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _events(response.text) == harness.rag.events
    assert harness.rag.calls == [("hi", "t-1", "alice")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected_detail"),
    [
        (ThreadAccessDeniedError("private"), "Not allowed to access this thread."),
        (ThreadNotFoundError("gone"), "Thread not found."),
        (ValueError("Message is too long."), "Message is too long."),
        (RuntimeError("database password is hunter2"), "Answer generation failed."),
    ],
)
async def test_stream_turns_failures_into_a_safe_error_event(
    harness: Harness, raised: Exception, expected_detail: str
) -> None:
    harness.rag.events = [{"type": "token", "delta": "partial"}]
    harness.rag.error = raised

    response = await harness.client.post(
        "/api/chat/stream", headers=harness.headers, json={"message": "hi"}
    )

    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1] == {"type": "error", "detail": expected_detail}
    assert "hunter2" not in response.text


@pytest.mark.asyncio
async def test_stream_requires_login(harness: Harness) -> None:
    response = await harness.client.post("/api/chat/stream", json={"message": "hi"})

    assert response.status_code == 401
    assert harness.rag.calls == []


@pytest.mark.asyncio
async def test_stream_says_503_when_no_openai_key_is_set() -> None:
    client, rag, engine = await _build(openai_api_key="")
    async with client:
        headers = await _login_headers(client)
        response = await client.post("/api/chat/stream", headers=headers, json={"message": "hi"})
    await engine.dispose()

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
    assert rag.calls == []


@pytest.mark.asyncio
async def test_routes_fail_loudly_when_the_rag_service_was_never_configured() -> None:
    app = FastAPI()
    app.state.rag_service = object()
    app.include_router(chat_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="RAG service is not configured"):
            await client.get("/api/threads")
