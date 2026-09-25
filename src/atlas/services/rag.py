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
from atlas.services.answer_cache import AnswerCache
from atlas.services.embeddings import EmbeddingClient
from atlas.services.hybrid import bm25_rank, fuse_hybrid
from atlas.services.observability import AskTrace
from atlas.services.prompting import (
    DEVELOPER_INSTRUCTIONS,
    build_user_payload,
    keep_close_chunks,
)
from atlas.services.tools import (
    ATLAS_TOOLS,
    parse_tool_arguments,
    run_allowlisted_tool,
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
        answer_cache: AnswerCache | None = None,
    ) -> None:
        self._settings = settings
        self._openai = openai_client
        self._session_factory = session_factory
        self._chunk_store = chunk_store
        self._embeddings = embeddings
        self._answer_cache = answer_cache

    async def list_threads(self, owner_id: str) -> list[ThreadOut]:
        async with self._session_factory() as session:
            threads = await ThreadRepository(session).list_threads(owner_id)
        return [
            ThreadOut(
                id=thread.id,
                title=thread.title,
                created_at=thread.created_at.isoformat(),
            )
            for thread in threads
        ]

    async def get_thread(self, thread_id: str, owner_id: str) -> ThreadDetailOut:
        async with self._session_factory() as session:
            thread = await ThreadRepository(session).get_owned_thread(thread_id, owner_id)
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

    async def create_thread(self, owner_id: str, title: str = "New chat") -> ThreadOut:
        async with self._session_factory() as session:
            thread = await ThreadRepository(session).create_thread(title, owner_id)
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
        vector_hits = keep_close_chunks(raw_hits, self._settings.max_distance)
        if not self._settings.hybrid_search_enabled:
            return vector_hits
        corpus = await _in_thread(self._chunk_store.list_chunks)
        lexical_hits = await _in_thread(
            bm25_rank,
            question,
            corpus,
            self._settings.retrieve_k,
        )
        return fuse_hybrid(
            vector_hits,
            lexical_hits,
            limit=self._settings.retrieve_k,
            rrf_k=self._settings.hybrid_rrf_k,
        )

    async def answer_once(self, question: str) -> tuple[list[RetrievedChunk], str]:
        """Retrieve and generate one turn without saving chat history.

        Used by the golden-set eval so scoring does not pollute SQLite threads.
        """
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty.")

        sources = await self.retrieve(cleaned)
        response = await self._openai.responses.create(
            model=self._settings.openai_chat_model,
            instructions=DEVELOPER_INSTRUCTIONS,
            temperature=0,
            input=cast(
                ResponseInputParam,
                [
                    {
                        "role": "user",
                        "content": build_user_payload(cleaned, sources),
                    }
                ],
            ),
        )
        answer = response.output_text.strip()
        if not answer:
            answer = "I could not generate an answer from the retrieved documents."
        return sources, answer

    async def run_ask(
        self,
        question: str,
        thread_id: str | None,
        owner_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty.")

        trace = AskTrace(owner_id=owner_id, model=self._settings.openai_chat_model)
        trace.record_question_length(cleaned)
        try:
            thread = await self._ensure_thread(thread_id, cleaned, owner_id)
            trace.record_thread(thread.id)
            async with self._session_factory() as session:
                repo = ThreadRepository(session)
                history = await repo.last_messages(
                    thread.id,
                    self._settings.history_window,
                )
                await repo.add_message(thread.id, "user", cleaned)

            sources = await self.retrieve(cleaned)
            trace.record_retrieve(sources)
            yield {"type": "thread", "thread": thread.model_dump()}
            yield {
                "type": "sources",
                "sources": [chunk.model_dump() for chunk in sources],
            }

            history_pairs = [
                (item.role, item.content)
                for item in history
                if item.role in {"user", "assistant"}
            ]
            cache_key: str | None = None
            if (
                self._answer_cache is not None
                and self._settings.answer_cache_enabled
            ):
                cache_key = self._answer_cache.build_key(
                    question=cleaned,
                    sources=sources,
                    history=history_pairs,
                    model=self._settings.openai_chat_model,
                    instructions=DEVELOPER_INSTRUCTIONS,
                )
                cached = await self._answer_cache.get(cache_key)
                if cached is not None:
                    answer = str(cached["answer"]).strip()
                    yield {"type": "token", "text": answer}
                    async with self._session_factory() as session:
                        await ThreadRepository(session).add_message(
                            thread.id, "assistant", answer
                        )
                    yield {"type": "done", "answer": answer}
                    trace.complete(answer)
                    return

            prompt_messages: list[EasyInputMessageParam] = _history_as_input(history)
            prompt_messages.append(
                {
                    "role": "user",
                    "content": build_user_payload(cleaned, sources),
                }
            )
            answer = ""
            used_tools = False
            async for event in self._call_chat_model(prompt_messages, trace):
                if event.get("type") == "tool":
                    used_tools = True
                if event.get("type") == "token":
                    answer += str(event.get("text", ""))
                yield event
            answer = answer.strip()
            if not answer:
                answer = "I could not generate an answer from the retrieved documents."
                yield {"type": "token", "text": answer}

            if (
                cache_key is not None
                and self._answer_cache is not None
                and not used_tools
            ):
                await self._answer_cache.set(cache_key, answer=answer, sources=sources)

            async with self._session_factory() as session:
                await ThreadRepository(session).add_message(thread.id, "assistant", answer)
            yield {"type": "done", "answer": answer}
            trace.complete(answer)
        except Exception as exc:
            trace.record_error(exc)
            trace.complete("")
            raise

    async def _ensure_thread(
        self,
        thread_id: str | None,
        question: str,
        owner_id: str,
    ) -> ThreadOut:
        if thread_id:
            existing = await self.get_thread(thread_id, owner_id)
            return ThreadOut(
                id=existing.id,
                title=existing.title,
                created_at=existing.created_at,
            )
        title = question if len(question) <= 72 else f"{question[:69]}..."
        return await self.create_thread(owner_id, title)

    async def _call_chat_model(
        self,
        prompt_messages: list[EasyInputMessageParam],
        trace: AskTrace,
    ) -> AsyncIterator[dict[str, Any]]:
        previous_response_id: str | None = None
        tool_outputs: list[dict[str, str]] = []
        max_rounds = self._settings.max_tool_rounds
        if max_rounds < 1:
            max_rounds = 1

        for _round in range(max_rounds + 1):
            text_parts, response = await self._complete_model_round(
                prompt_messages,
                previous_response_id,
                tool_outputs,
            )
            trace.record_usage(response)
            previous_response_id = str(getattr(response, "id", "") or "")
            function_calls = _function_calls(response)
            if not function_calls:
                for part in text_parts:
                    yield {"type": "token", "text": part}
                return

            tool_outputs = []
            for call in function_calls:
                name = str(getattr(call, "name", "") or "")
                arguments = str(getattr(call, "arguments", "") or "{}")
                call_id = str(getattr(call, "call_id", "") or "")
                result = run_allowlisted_tool(name, arguments)
                trace.record_tool(name)
                yield {
                    "type": "tool",
                    "name": name,
                    "arguments": parse_tool_arguments(arguments),
                    "result": result,
                }
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result,
                    }
                )
            if not previous_response_id:
                break

        fallback = "The tool loop stopped before a final answer was written."
        yield {"type": "token", "text": fallback}

    async def _complete_model_round(
        self,
        prompt_messages: list[EasyInputMessageParam],
        previous_response_id: str | None,
        tool_outputs: list[dict[str, str]],
    ) -> tuple[list[str], Any]:
        text_parts: list[str] = []
        if previous_response_id:
            stream_cm = self._openai.responses.stream(
                model=self._settings.openai_chat_model,
                instructions=DEVELOPER_INSTRUCTIONS,
                tools=cast(Any, ATLAS_TOOLS),
                previous_response_id=previous_response_id,
                input=cast(ResponseInputParam, tool_outputs),
            )
        else:
            stream_cm = self._openai.responses.stream(
                model=self._settings.openai_chat_model,
                instructions=DEVELOPER_INSTRUCTIONS,
                tools=cast(Any, ATLAS_TOOLS),
                input=cast(ResponseInputParam, prompt_messages),
            )
        async with stream_cm as stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    text_parts.append(event.delta)
            response = await stream.get_final_response()
        return text_parts, response


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


def _function_calls(response: Any) -> list[Any]:
    output = getattr(response, "output", None) or []
    return [item for item in output if getattr(item, "type", None) == "function_call"]
