import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import openai
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from atlas.config import Settings
from atlas.schemas.chat import RetrievedChunk
from atlas.services.rag import RagChatService
from atlas.services.rerank import (
    RERANK_INSTRUCTIONS,
    ChunkScore,
    LlmReranker,
    RerankResult,
    build_rerank_payload,
    order_by_scores,
)


def _chunk(index: int, text: str = "", distance: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc",
        document_title="Handbook",
        chunk_index=index,
        text=text or f"paragraph {index}",
        distance=distance,
    )


def _scores(*pairs: tuple[int, int]) -> list[ChunkScore]:
    return [ChunkScore(id=chunk_id, score=score) for chunk_id, score in pairs]


class StubResponses:
    def __init__(
        self,
        parsed: RerankResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._parsed = parsed
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(output_parsed=self._parsed)


def _reranker(responses: StubResponses) -> LlmReranker:
    client = cast(AsyncOpenAI, SimpleNamespace(responses=responses))
    return LlmReranker(client, "test-rerank-model")


def test_order_by_scores_puts_highest_score_first_and_keeps_limit() -> None:
    candidates = [_chunk(0), _chunk(1), _chunk(2), _chunk(3)]

    ordered = order_by_scores(candidates, _scores((1, 2), (2, 9), (3, 5), (4, 7)), 2)

    assert [chunk.chunk_index for chunk in ordered] == [1, 3]


def test_order_by_scores_ties_keep_merged_order() -> None:
    candidates = [_chunk(0), _chunk(1), _chunk(2)]

    ordered = order_by_scores(candidates, _scores((1, 5), (2, 5), (3, 5)), 3)

    assert [chunk.chunk_index for chunk in ordered] == [0, 1, 2]


def test_order_by_scores_ignores_unknown_ids_and_puts_unscored_last() -> None:
    candidates = [_chunk(0), _chunk(1), _chunk(2)]

    ordered = order_by_scores(candidates, _scores((0, 10), (99, 10), (3, 4)), 3)

    assert [chunk.chunk_index for chunk in ordered] == [2, 0, 1]


def test_order_by_scores_uses_first_score_when_an_id_repeats() -> None:
    candidates = [_chunk(0), _chunk(1)]

    ordered = order_by_scores(candidates, _scores((1, 1), (1, 10), (2, 5)), 2)

    assert [chunk.chunk_index for chunk in ordered] == [1, 0]


def test_order_by_scores_zero_limit_returns_nothing() -> None:
    assert order_by_scores([_chunk(0)], _scores((1, 9)), 0) == []


def test_build_rerank_payload_numbers_each_chunk_and_caps_length() -> None:
    long_text = "x" * 5000
    payload = build_rerank_payload("what is it?", [_chunk(0, "short"), _chunk(1, long_text)])

    assert payload.startswith("<user_query>\nwhat is it?\n</user_query>")
    assert '<candidate id="1">\nshort\n</candidate>' in payload
    assert '<candidate id="2">' in payload
    assert "x" * 1200 in payload
    assert "x" * 1201 not in payload


@pytest.mark.asyncio
async def test_rerank_reorders_and_asks_the_model_once() -> None:
    responses = StubResponses(RerankResult(scores=_scores((1, 1), (2, 9), (3, 4))))
    candidates = [_chunk(0), _chunk(1), _chunk(2)]

    ranked = await _reranker(responses).rerank("question", candidates, 2)

    assert [chunk.chunk_index for chunk in ranked] == [1, 2]
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call["model"] == "test-rerank-model"
    assert call["instructions"] == RERANK_INSTRUCTIONS
    assert call["text_format"] is RerankResult
    assert call["temperature"] == 0
    assert "<user_query>\nquestion\n</user_query>" in call["input"]


@pytest.mark.asyncio
async def test_rerank_keeps_match_labels() -> None:
    responses = StubResponses(RerankResult(scores=_scores((1, 1), (2, 9))))
    both = _chunk(0).model_copy(update={"match": "both"})
    lexical = _chunk(1).model_copy(update={"match": "lexical"})

    ranked = await _reranker(responses).rerank("question", [both, lexical], 2)

    assert [(chunk.chunk_index, chunk.match) for chunk in ranked] == [
        (1, "lexical"),
        (0, "both"),
    ]


@pytest.mark.asyncio
async def test_rerank_skips_the_model_when_there_is_nothing_to_rank() -> None:
    responses = StubResponses(RerankResult(scores=[]))
    reranker = _reranker(responses)

    assert await reranker.rerank("question", [], 5) == []
    assert await reranker.rerank("question", [_chunk(0), _chunk(1)], 0) == []
    single = await reranker.rerank("question", [_chunk(0)], 5)

    assert [chunk.chunk_index for chunk in single] == [0]
    assert responses.calls == []


@pytest.mark.asyncio
async def test_rerank_keeps_merged_order_when_openai_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reranker = _reranker(StubResponses(error=openai.OpenAIError("connection dropped")))

    with caplog.at_level(logging.WARNING, logger="atlas.rerank"):
        ranked = await reranker.rerank("question", [_chunk(0), _chunk(1), _chunk(2)], 2)

    assert [chunk.chunk_index for chunk in ranked] == [0, 1]
    assert "OpenAIError" in caplog.text
    assert "connection dropped" not in caplog.text


@pytest.mark.asyncio
async def test_rerank_keeps_merged_order_when_scores_do_not_parse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ValidationError) as caught:
        RerankResult.model_validate({"scores": "not a list"})
    reranker = _reranker(StubResponses(error=caught.value))

    with caplog.at_level(logging.WARNING, logger="atlas.rerank"):
        ranked = await reranker.rerank("question", [_chunk(0), _chunk(1)], 2)

    assert [chunk.chunk_index for chunk in ranked] == [0, 1]
    assert "ValidationError" in caplog.text


@pytest.mark.asyncio
async def test_rerank_keeps_merged_order_when_model_returns_no_scores(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reranker = _reranker(StubResponses(parsed=None))

    with caplog.at_level(logging.WARNING, logger="atlas.rerank"):
        ranked = await reranker.rerank("question", [_chunk(0), _chunk(1), _chunk(2)], 2)

    assert [chunk.chunk_index for chunk in ranked] == [0, 1]
    assert "no scores" in caplog.text


class FakeChunkStore:
    """Vector search finds chunks 0 and 1. Keyword search finds chunks 2 and 3."""

    def __init__(self) -> None:
        self._vector_hits = [_chunk(0, "alpha", 0.1), _chunk(1, "beta", 0.2)]
        self._corpus = [
            _chunk(0, "alpha"),
            _chunk(1, "beta"),
            _chunk(2, "zebra crossing rules"),
            _chunk(3, "zebra stripes"),
        ]

    def query(self, embedding: Sequence[float], limit: int) -> list[RetrievedChunk]:
        return self._vector_hits[:limit]

    def list_chunks(self) -> list[RetrievedChunk]:
        return list(self._corpus)


class FakeEmbeddings:
    async def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class RecordingReranker:
    def __init__(self) -> None:
        self.received: list[list[int]] = []
        self.limits: list[int] = []

    async def rerank(
        self,
        question: str,
        candidates: Sequence[RetrievedChunk],
        limit: int,
    ) -> list[RetrievedChunk]:
        self.received.append([chunk.chunk_index for chunk in candidates])
        self.limits.append(limit)
        return list(reversed(candidates))[:limit]


def _service(
    *,
    hybrid: bool = True,
    reranker: RecordingReranker | None = None,
) -> RagChatService:
    settings = Settings(
        openai_api_key="test-key",
        retrieve_k=2,
        max_distance=0.85,
        hybrid_search_enabled=hybrid,
    )
    return RagChatService(
        settings,
        cast(Any, None),
        cast(Any, None),
        cast(Any, FakeChunkStore()),
        cast(Any, FakeEmbeddings()),
        reranker=reranker,
    )


@pytest.mark.asyncio
async def test_retrieve_hands_the_whole_merge_to_the_reranker() -> None:
    reranker = RecordingReranker()

    hits = await _service(reranker=reranker).retrieve("zebra")

    assert len(reranker.received) == 1
    assert sorted(reranker.received[0]) == [0, 1, 2, 3]
    assert reranker.limits == [2]
    assert len(hits) == 2
    assert hits[0].chunk_index == reranker.received[0][-1]


@pytest.mark.asyncio
async def test_retrieve_without_a_reranker_keeps_the_merge_at_retrieve_k() -> None:
    hits = await _service().retrieve("zebra")

    assert len(hits) == 2


@pytest.mark.asyncio
async def test_retrieve_skips_the_reranker_when_hybrid_is_off() -> None:
    reranker = RecordingReranker()

    hits = await _service(hybrid=False, reranker=reranker).retrieve("zebra")

    assert reranker.received == []
    assert [chunk.chunk_index for chunk in hits] == [0, 1]
    assert all(chunk.match == "vector" for chunk in hits)
