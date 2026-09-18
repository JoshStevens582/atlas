"""Safe Ask traces: metadata and IDs, never secrets or full prompts."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas.schemas.chat import RetrievedChunk

logger = logging.getLogger("atlas.ask")

_TRACE_LOG_PATH = Path("data/ask_traces.log")

# Fields that must never appear in a log line (defence in depth).
_FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "authorization",
        "api_key",
        "openai_api_key",
        "prompt",
        "message",
        "question",
        "answer",
        "content",
    }
)


def new_request_id() -> str:
    return str(uuid4())


def safe_log_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop known secret / body keys so callers cannot leak them by mistake."""
    return {key: value for key, value in fields.items() if key.lower() not in _FORBIDDEN_KEYS}


def _emit(event: str, fields: dict[str, Any]) -> None:
    payload = safe_log_fields(fields)
    line = f"{event} {payload}"
    logger.info("%s", line)
    _TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _TRACE_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


class AskTrace:
    """One user question: retrieve → tools → generate, tied by request_id."""

    def __init__(self, owner_id: str, model: str) -> None:
        self.request_id = new_request_id()
        self.owner_id = owner_id
        self.model = model
        self.thread_id: str | None = None
        self.chunk_ids: list[str] = []
        self.chunk_titles: list[str] = []
        self.tool_names: list[str] = []
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.question_chars: int = 0
        self.answer_chars: int = 0
        self.error: str | None = None
        self._started = time.perf_counter()

    def record_question_length(self, question: str) -> None:
        self.question_chars = len(question)

    def record_thread(self, thread_id: str) -> None:
        self.thread_id = thread_id

    def record_retrieve(self, chunks: list[RetrievedChunk]) -> None:
        self.chunk_ids = [f"{chunk.document_id}:{chunk.chunk_index}" for chunk in chunks]
        self.chunk_titles = [chunk.document_title for chunk in chunks]
        _emit(
            "ask_retrieve",
            {
                "request_id": self.request_id,
                "owner_id": self.owner_id,
                "thread_id": self.thread_id,
                "chunk_ids": self.chunk_ids,
                "chunk_titles": self.chunk_titles,
                "hit_count": len(chunks),
            },
        )

    def record_tool(self, name: str) -> None:
        self.tool_names.append(name)
        _emit(
            "ask_tool",
            {
                "request_id": self.request_id,
                "owner_id": self.owner_id,
                "thread_id": self.thread_id,
                "tool_name": name,
            },
        )

    def record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if input_tokens is None:
            input_tokens = getattr(usage, "prompt_tokens", None)
        if output_tokens is None:
            output_tokens = getattr(usage, "completion_tokens", None)
        if isinstance(input_tokens, int):
            self.input_tokens = (self.input_tokens or 0) + input_tokens
        if isinstance(output_tokens, int):
            self.output_tokens = (self.output_tokens or 0) + output_tokens

    def record_error(self, exc: BaseException) -> None:
        self.error = type(exc).__name__

    def complete(self, answer: str) -> None:
        self.answer_chars = len(answer)
        latency_ms = int((time.perf_counter() - self._started) * 1000)
        _emit(
            "ask_complete",
            {
                "request_id": self.request_id,
                "owner_id": self.owner_id,
                "thread_id": self.thread_id,
                "model": self.model,
                "latency_ms": latency_ms,
                "question_chars": self.question_chars,
                "answer_chars": self.answer_chars,
                "chunk_ids": self.chunk_ids,
                "tool_names": self.tool_names,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "error": self.error,
            },
        )
