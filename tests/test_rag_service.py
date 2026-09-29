from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import (
    ThreadAccessDeniedError,
    ThreadNotFoundError,
    ThreadRepository,
)
from atlas.schemas.chat import RetrievedChunk
from atlas.services.answer_cache import AnswerCache
from atlas.services.embeddings import EmbeddingClient
from atlas.services.rag import RagChatService
from atlas.services.tools import ONLY_ONE_TICKET_TOOL

NO_ANSWER = "I could not generate an answer from the retrieved documents."
LOOP_STOPPED = "The tool loop stopped before a final answer was written."


class StubStream:
    """Stands in for `openai.responses.stream(...)`: text deltas, then a final response."""

    def __init__(
        self,
        parts: list[str],
        final: Any,
        *,
        fail: Exception | None = None,
    ) -> None:
        self._parts = parts
        self._final = final
        self._fail = fail

    async def __aenter__(self) -> "StubStream":
        if self._fail is not None:
            raise self._fail
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._events()

    async def _events(self) -> AsyncIterator[Any]:
        yield SimpleNamespace(type="response.created")
        for part in self._parts:
            yield SimpleNamespace(type="response.output_text.delta", delta=part)

    async def get_final_response(self) -> Any:
        return self._final


def _final(response_id: str = "resp-1", calls: Sequence[Any] = ()) -> Any:
    return SimpleNamespace(
        id=response_id,
        output=[SimpleNamespace(type="message"), *calls],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4),
    )


def _call(name: str, arguments: str = "{}", call_id: str = "call-1") -> Any:
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments,
        call_id=call_id,
    )


class StubOpenAI:
    def __init__(self, streams: list[StubStream], create_text: str = "Northstar [1]") -> None:
        self._streams = streams
        self._create_text = create_text
        self.stream_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.responses = self

    def stream(self, **kwargs: Any) -> StubStream:
        self.stream_calls.append(kwargs)
        return self._streams.pop(0)

    async def create(self, **kwargs: Any) -> Any:
        self.create_calls.append(kwargs)
        return SimpleNamespace(output_text=self._create_text)


class FakeChunkStore:
    def query(self, embedding: Sequence[float], limit: int) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                document_id="doc",
                document_title="Demo Note",
                chunk_index=0,
                text="The project codename is Northstar.",
                distance=0.3,
            )
        ]

    def list_chunks(self) -> list[RetrievedChunk]:
        return []


class FakeEmbeddings:
    async def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class StubAnswerCache:
    def __init__(self, cached: dict[str, Any] | None = None) -> None:
        self._cached = cached
        self.gets: list[str] = []
        self.stored: list[tuple[str, str]] = []

    def build_key(self, **kwargs: Any) -> str:
        return "key-1"

    async def get(self, key: str) -> dict[str, Any] | None:
        self.gets.append(key)
        return self._cached

    async def set(self, key: str, *, answer: str, sources: list[RetrievedChunk]) -> None:
        self.stored.append((key, answer))


@pytest.fixture(autouse=True)
def _trace_log_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("atlas.services.observability._TRACE_LOG_PATH", tmp_path / "ask.log")


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


def _service(
    factory: async_sessionmaker[AsyncSession],
    openai: StubOpenAI,
    *,
    cache: StubAnswerCache | None = None,
    **overrides: Any,
) -> RagChatService:
    settings = Settings(openai_api_key="test-key", hybrid_search_enabled=False, **overrides)
    return RagChatService(
        settings,
        cast(AsyncOpenAI, openai),
        factory,
        cast(ChromaChunkStore, FakeChunkStore()),
        cast(EmbeddingClient, FakeEmbeddings()),
        answer_cache=cast(AnswerCache | None, cache),
    )


async def _ask(
    service: RagChatService,
    question: str,
    *,
    thread_id: str | None = None,
    owner: str = "alice",
) -> list[dict[str, Any]]:
    return [event async for event in service.run_ask(question, thread_id, owner)]


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [str(event["type"]) for event in events]


@pytest.mark.asyncio
async def test_run_ask_streams_the_answer_and_saves_both_messages(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI([StubStream(["Northstar ", "[1]"], _final())])
    service = _service(factory, openai)

    events = await _ask(service, "What is the project codename?")

    assert _types(events) == ["thread", "sources", "token", "token", "sources", "done"]
    assert events[-1]["answer"] == "Northstar [1]"
    assert events[1]["sources"][0]["cited"] is None
    assert events[4]["sources"][0]["cited"] is True
    thread_id = events[0]["thread"]["id"]
    saved = await service.get_thread(thread_id, "alice")
    assert [(message.role, message.content) for message in saved.messages] == [
        ("user", "What is the project codename?"),
        ("assistant", "Northstar [1]"),
    ]
    first_call = openai.stream_calls[0]
    assert "<context>" in first_call["input"][-1]["content"]
    assert "The project codename is Northstar." in first_call["input"][-1]["content"]
    assert first_call["tools"]


@pytest.mark.asyncio
async def test_run_ask_rejects_a_blank_question(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(factory, StubOpenAI([]))

    with pytest.raises(ValueError, match="empty"):
        await _ask(service, "   ")


@pytest.mark.asyncio
async def test_run_ask_shortens_a_long_question_into_the_thread_title(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(factory, StubOpenAI([StubStream(["ok"], _final())]))
    question = "q" * 100

    events = await _ask(service, question)

    assert events[0]["thread"]["title"] == "q" * 69 + "..."


@pytest.mark.asyncio
async def test_run_ask_sends_earlier_turns_but_not_system_rows(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI([StubStream(["One"], _final()), StubStream(["Two"], _final())])
    service = _service(factory, openai)
    first = await _ask(service, "first question")
    thread_id = first[0]["thread"]["id"]
    async with factory() as session:
        await ThreadRepository(session).add_message(thread_id, "system", "hidden note")

    await _ask(service, "second question", thread_id=thread_id)

    sent = openai.stream_calls[1]["input"]
    assert [(item["role"], item["content"]) for item in sent[:2]] == [
        ("user", "first question"),
        ("assistant", "One"),
    ]
    assert all(item["content"] != "hidden note" for item in sent)
    assert "second question" in sent[-1]["content"]


@pytest.mark.asyncio
async def test_run_ask_refuses_someone_elses_thread_and_unknown_threads(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(factory, StubOpenAI([]))
    thread = await service.create_thread("alice")

    with pytest.raises(ThreadAccessDeniedError):
        await _ask(service, "hello", thread_id=thread.id, owner="bob")
    with pytest.raises(ThreadNotFoundError):
        await _ask(service, "hello", thread_id="missing", owner="alice")


@pytest.mark.asyncio
async def test_run_ask_uses_a_fallback_when_the_model_writes_nothing(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(factory, StubOpenAI([StubStream([], _final())]))

    events = await _ask(service, "What is the project codename?")

    assert events[2] == {"type": "token", "text": NO_ANSWER}
    assert events[-1]["answer"] == NO_ANSWER


@pytest.mark.asyncio
async def test_run_ask_runs_the_ticket_tool_then_writes_the_answer(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI(
        [
            StubStream([], _final("resp-1", [_call("list_support_tickets")])),
            StubStream(["Three tickets."], _final("resp-2")),
        ]
    )
    service = _service(factory, openai)

    events = await _ask(service, "List all support tickets")

    tool_events = [event for event in events if event["type"] == "tool"]
    assert [event["name"] for event in tool_events] == ["list_support_tickets"]
    assert "T-104" in tool_events[0]["result"]
    assert events[-1]["answer"] == "Three tickets."
    second_call = openai.stream_calls[1]
    assert second_call["previous_response_id"] == "resp-1"
    assert second_call["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": tool_events[0]["result"],
        }
    ]


@pytest.mark.asyncio
async def test_run_ask_refuses_a_tool_the_model_made_up_and_keeps_going(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI(
        [
            StubStream([], _final("resp-1", [_call("delete_everything")])),
            StubStream(["I cannot do that."], _final("resp-2")),
        ]
    )

    events = await _ask(_service(factory, openai), "Delete every ticket")

    tool_events = [event for event in events if event["type"] == "tool"]
    assert [event["name"] for event in tool_events] == ["delete_everything"]
    assert "Unknown tool" in tool_events[0]["result"]
    assert events[-1]["answer"] == "I cannot do that."


@pytest.mark.asyncio
async def test_run_ask_turns_a_list_call_into_a_lookup_when_one_ticket_is_named(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI(
        [
            StubStream([], _final("resp-1", [_call("list_support_tickets")])),
            StubStream(["In transit."], _final("resp-2")),
        ]
    )
    service = _service(factory, openai)

    events = await _ask(service, "Where is support ticket T-104?")

    tool_events = [event for event in events if event["type"] == "tool"]
    assert [event["name"] for event in tool_events] == ["get_support_ticket"]
    assert tool_events[0]["arguments"] == {"ticket_id": "T-104"}


@pytest.mark.asyncio
async def test_run_ask_allows_only_one_ticket_tool_per_ask(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    calls = [
        _call("list_support_tickets", call_id="call-list"),
        _call("get_support_ticket", '{"ticket_id": "T-201"}', call_id="call-get"),
    ]
    openai = StubOpenAI(
        [
            StubStream([], _final("resp-1", calls)),
            StubStream(["Done."], _final("resp-2")),
        ]
    )
    service = _service(factory, openai)

    events = await _ask(service, "Compare our tickets")

    tool_events = [event for event in events if event["type"] == "tool"]
    assert [event["name"] for event in tool_events] == ["get_support_ticket"]
    outputs = openai.stream_calls[1]["input"]
    assert outputs[1] == {
        "type": "function_call_output",
        "call_id": "call-list",
        "output": ONLY_ONE_TICKET_TOOL,
    }


@pytest.mark.asyncio
async def test_run_ask_stops_when_the_response_has_no_id_to_continue_from(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI([StubStream([], _final("", [_call("list_support_tickets")]))])
    service = _service(factory, openai)

    events = await _ask(service, "List all support tickets")

    assert events[-1]["answer"] == LOOP_STOPPED
    assert len(openai.stream_calls) == 1


@pytest.mark.asyncio
async def test_run_ask_stops_when_the_model_keeps_asking_for_tools(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI(
        [
            StubStream([], _final("resp-1", [_call("list_support_tickets")])),
            StubStream([], _final("resp-2", [_call("list_support_tickets", call_id="call-2")])),
        ]
    )
    service = _service(factory, openai, max_tool_rounds=1)

    events = await _ask(service, "List all support tickets")

    assert events[-1]["answer"] == LOOP_STOPPED
    assert len(openai.stream_calls) == 2


@pytest.mark.asyncio
async def test_run_ask_still_calls_the_model_once_when_tool_rounds_is_zero(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI([StubStream(["Northstar [1]"], _final())])
    service = _service(factory, openai, max_tool_rounds=0)

    events = await _ask(service, "What is the project codename?")

    assert events[-1]["answer"] == "Northstar [1]"


@pytest.mark.asyncio
async def test_run_ask_reraises_when_the_model_call_fails(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    failing = StubStream([], _final(), fail=RuntimeError("model down"))
    service = _service(factory, StubOpenAI([failing]))

    with pytest.raises(RuntimeError, match="model down"):
        await _ask(service, "What is the project codename?")


@pytest.mark.asyncio
async def test_run_ask_returns_a_cached_answer_without_calling_the_model(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI([])
    cache = StubAnswerCache({"answer": "Cached: Northstar [1]"})
    service = _service(factory, openai, cache=cache)

    events = await _ask(service, "What is the project codename?")

    assert openai.stream_calls == []
    assert _types(events) == ["thread", "sources", "sources", "token", "done"]
    assert events[2]["sources"][0]["cited"] is True
    assert events[-1]["answer"] == "Cached: Northstar [1]"
    saved = await service.get_thread(events[0]["thread"]["id"], "alice")
    assert saved.messages[-1].content == "Cached: Northstar [1]"


@pytest.mark.asyncio
async def test_run_ask_stores_a_handbook_answer_in_the_cache(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cache = StubAnswerCache()
    service = _service(
        factory,
        StubOpenAI([StubStream(["Northstar [1]"], _final())]),
        cache=cache,
    )

    await _ask(service, "What is the project codename?")

    assert cache.gets == ["key-1"]
    assert cache.stored == [("key-1", "Northstar [1]")]


@pytest.mark.asyncio
async def test_run_ask_does_not_cache_an_answer_that_used_a_tool(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cache = StubAnswerCache()
    openai = StubOpenAI(
        [
            StubStream([], _final("resp-1", [_call("list_support_tickets")])),
            StubStream(["Three tickets."], _final("resp-2")),
        ]
    )
    service = _service(factory, openai, cache=cache)

    await _ask(service, "List all support tickets")

    assert cache.stored == []


@pytest.mark.asyncio
async def test_run_ask_skips_the_cache_when_it_is_turned_off(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cache = StubAnswerCache()
    service = _service(
        factory,
        StubOpenAI([StubStream(["Northstar [1]"], _final())]),
        cache=cache,
        answer_cache_enabled=False,
    )

    await _ask(service, "What is the project codename?")

    assert cache.gets == []
    assert cache.stored == []


@pytest.mark.asyncio
async def test_answer_once_marks_the_cards_the_answer_used(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    openai = StubOpenAI([], create_text="  Northstar [1]  ")
    service = _service(factory, openai)

    sources, answer = await service.answer_once("What is the project codename?")

    assert answer == "Northstar [1]"
    assert sources[0].cited is True
    assert openai.create_calls[0]["temperature"] == 0


@pytest.mark.asyncio
async def test_answer_once_rejects_blank_questions_and_fills_an_empty_answer(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(factory, StubOpenAI([], create_text="   "))

    with pytest.raises(ValueError, match="empty"):
        await service.answer_once("  ")
    _, answer = await service.answer_once("What is the project codename?")

    assert answer == NO_ANSWER


@pytest.mark.asyncio
async def test_threads_are_listed_per_owner_and_read_back_in_order(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(factory, StubOpenAI([]))
    mine = await service.create_thread("alice", "Mine")
    await service.create_thread("bob", "Theirs")
    async with factory() as session:
        repo = ThreadRepository(session)
        await repo.add_message(mine.id, "user", "first")
        await repo.add_message(mine.id, "assistant", "second")

    listed = await service.list_threads("alice")
    detail = await service.get_thread(mine.id, "alice")

    assert [thread.title for thread in listed] == ["Mine"]
    assert [message.content for message in detail.messages] == ["first", "second"]
    with pytest.raises(ThreadAccessDeniedError):
        await service.get_thread(mine.id, "bob")
