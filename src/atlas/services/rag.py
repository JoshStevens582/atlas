import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.responses import EasyInputMessageParam
from openai.types.responses.response_input_param import ResponseInputParam
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.db.models import ChatMessage
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.sql_repo import ThreadRepository
from atlas.schemas.chat import ChatMessageOut, RetrievedChunk, ThreadDetailOut, ThreadOut
from atlas.services.embeddings import EmbeddingClient
from atlas.services.prompting import (
    DEVELOPER_INSTRUCTIONS,
    build_user_payload,
    keep_close_chunks,
)


# Retrieve-then-generate chat service for Atlas.
class RagChatService:
    def __init__(
        self,
        settings: Settings,
        openai_client: AsyncOpenAI,
        session_factory: async_sessionmaker[AsyncSession],
        chunk_store: ChromaChunkStore,
        embeddings: EmbeddingClient,
    ) -> None:
        self._settings = settings
        self._openai = openai_client
        self._session_factory = session_factory
        self._chunk_store = chunk_store
        self._embeddings = embeddings

    async def list_threads(self) -> list[ThreadOut]:
        async with self._session_factory() as session:
            threads = await ThreadRepository(session).list_threads()
        return [
            ThreadOut(
                id=thread.id,
                title=thread.title,
                created_at=thread.created_at.isoformat(),
            )
            for thread in threads
        ]

    async def get_thread(self, thread_id: str) -> ThreadDetailOut | None:
        async with self._session_factory() as session:
            thread = await ThreadRepository(session).get_thread(thread_id)
        if thread is None:
            return None
        messages = sorted(thread.messages, key=lambda item: item.created_at)
        return ThreadDetailOut(
            id=thread.id,
            title=thread.title,
            created_at=thread.created_at.isoformat(),
            messages=[
                ChatMessageOut(
                    id=message.id,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at.isoformat(),
                )
                for message in messages
            ],
        )

    async def create_thread(self, title: str = "New chat") -> ThreadOut:
        async with self._session_factory() as session:
            thread = await ThreadRepository(session).create_thread(title)
        return ThreadOut(
            id=thread.id,
            title=thread.title,
            created_at=thread.created_at.isoformat(),
        )

    async def retrieve(self, question: str) -> list[RetrievedChunk]:
        query_vector = await self._embeddings.embed_query(question)
        raw_hits = await _in_thread(
            self._chunk_store.query,
            query_vector,
            self._settings.retrieve_k,
        )
        return keep_close_chunks(raw_hits, self._settings.max_distance)

    async def stream_answer(
        self,
        question: str,
        thread_id: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty.")

        thread = await self._ensure_thread(thread_id, cleaned)
        async with self._session_factory() as session:
            repo = ThreadRepository(session)
            history = await repo.last_messages(
                thread.id,
                self._settings.history_window,
            )
            await repo.add_message(thread.id, "user", cleaned)

        sources = await self.retrieve(cleaned)
        yield {"type": "thread", "thread": thread.model_dump()}
        yield {
            "type": "sources",
            "sources": [chunk.model_dump() for chunk in sources],
        }

        prompt_messages: list[EasyInputMessageParam] = _history_as_input(history)
        prompt_messages.append(
            {
                "role": "user",
                "content": build_user_payload(cleaned, sources),
            }
        )
        answer_parts: list[str] = []
        async with self._openai.responses.stream(
            model=self._settings.openai_chat_model,
            instructions=DEVELOPER_INSTRUCTIONS,
            input=cast(ResponseInputParam, prompt_messages),
        ) as stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    answer_parts.append(event.delta)
                    yield {"type": "token", "text": event.delta}

        answer = "".join(answer_parts).strip()
        if not answer:
            answer = "I could not generate an answer from the retrieved documents."
            yield {"type": "token", "text": answer}

        async with self._session_factory() as session:
            await ThreadRepository(session).add_message(thread.id, "assistant", answer)
        yield {"type": "done", "answer": answer}

    async def _ensure_thread(self, thread_id: str | None, question: str) -> ThreadOut:
        if thread_id:
            existing = await self.get_thread(thread_id)
            if existing is None:
                raise LookupError("Thread not found.")
            return ThreadOut(
                id=existing.id,
                title=existing.title,
                created_at=existing.created_at,
            )
        title = question if len(question) <= 72 else f"{question[:69]}..."
        return await self.create_thread(title)


def _history_as_input(history: list[ChatMessage]) -> list[EasyInputMessageParam]:
    messages: list[EasyInputMessageParam] = []
    for item in history:
        if item.role not in {"user", "assistant"}:
            continue
        role: Any = item.role
        messages.append({"role": role, "content": item.content})
    return messages


async def _in_thread(func: Any, *args: Any) -> Any:
    return await asyncio.to_thread(func, *args)
