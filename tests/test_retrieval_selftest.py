import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import openai
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.schemas.chat import RetrievalCheckOut, RetrievedChunk
from atlas.schemas.eval import PassageQuestion, PassageQuestions
from atlas.services.retrieval_selftest import (
    QUESTION_INSTRUCTIONS,
    LlmQuestionWriter,
    RetrievalSelfTest,
    build_question_payload,
    pick_spread,
)

LONG = "x" * 250


def _chunk(index: int, text: str = LONG, document_id: str = "doc-1") -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document_id,
        document_title="Handbook",
        chunk_index=index,
        text=text,
        distance=0.2,
    )


def test_pick_spread_takes_everything_when_the_document_is_small() -> None:
    chunks = [_chunk(0), _chunk(1), _chunk(2)]

    assert pick_spread(chunks, 6) == chunks


def test_pick_spread_spaces_the_picks_from_start_to_end() -> None:
    chunks = [_chunk(index) for index in range(12)]

    picked = pick_spread(chunks, 4)

    assert [chunk.chunk_index for chunk in picked] == [0, 3, 6, 9]


def test_pick_spread_skips_short_fragments() -> None:
    chunks = [_chunk(0, "# Heading"), _chunk(1), _chunk(2, "tiny"), _chunk(3)]

    assert [chunk.chunk_index for chunk in pick_spread(chunks, 6)] == [1, 3]


@pytest.mark.parametrize("sample_size", [0, -2])
def test_pick_spread_picks_nothing_for_a_sample_size_below_one(sample_size: int) -> None:
    assert pick_spread([_chunk(0)], sample_size) == []


def test_pick_spread_picks_nothing_when_every_chunk_is_short() -> None:
    assert pick_spread([_chunk(0, "short")], 6) == []


def test_build_question_payload_numbers_passages_and_caps_length() -> None:
    payload = build_question_payload(["first", "y" * 5000])

    assert '<passage id="1">\nfirst\n</passage>' in payload
    assert '<passage id="2">' in payload
    assert "y" * 1500 in payload
    assert "y" * 1501 not in payload


class StubResponses:
    def __init__(
        self,
        parsed: PassageQuestions | None = None,
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


def _writer(responses: StubResponses) -> LlmQuestionWriter:
    client = cast(AsyncOpenAI, SimpleNamespace(responses=responses))
    return LlmQuestionWriter(client, "test-question-model")


@pytest.mark.asyncio
async def test_writer_matches_each_question_to_its_passage_number() -> None:
    responses = StubResponses(
        PassageQuestions(
            questions=[
                PassageQuestion(passage_number=2, question="  Second question?  "),
                PassageQuestion(passage_number=1, question="First question?"),
            ]
        )
    )

    questions = await _writer(responses).write(["one", "two", "three"])

    assert questions == ["First question?", "Second question?", None]
    call = responses.calls[0]
    assert call["model"] == "test-question-model"
    assert call["instructions"] == QUESTION_INSTRUCTIONS
    assert call["text_format"] is PassageQuestions
    assert call["temperature"] == 0


@pytest.mark.asyncio
async def test_writer_ignores_bad_numbers_blank_questions_and_repeats() -> None:
    responses = StubResponses(
        PassageQuestions(
            questions=[
                PassageQuestion(passage_number=0, question="zero"),
                PassageQuestion(passage_number=9, question="too high"),
                PassageQuestion(passage_number=1, question="   "),
                PassageQuestion(passage_number=2, question="kept"),
                PassageQuestion(passage_number=2, question="a repeat"),
                PassageQuestion(passage_number=3, question="q" * 900),
            ]
        )
    )

    questions = await _writer(responses).write(["one", "two", "three"])

    assert questions == [None, "kept", "q" * 300]


@pytest.mark.asyncio
async def test_writer_does_not_call_the_model_for_no_passages() -> None:
    responses = StubResponses(PassageQuestions(questions=[]))

    assert await _writer(responses).write([]) == []
    assert responses.calls == []


@pytest.mark.asyncio
async def test_writer_returns_no_questions_when_openai_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = _writer(StubResponses(error=openai.OpenAIError("secret detail")))

    with caplog.at_level(logging.WARNING, logger="atlas.retrieval_selftest"):
        questions = await writer.write(["one", "two"])

    assert questions == [None, None]
    assert "OpenAIError" in caplog.text
    assert "secret detail" not in caplog.text


@pytest.mark.asyncio
async def test_writer_returns_no_questions_when_the_reply_does_not_parse() -> None:
    with pytest.raises(ValidationError) as caught:
        PassageQuestions.model_validate({"questions": "nope"})

    questions = await _writer(StubResponses(error=caught.value)).write(["one"])

    assert questions == [None]


@pytest.mark.asyncio
async def test_writer_returns_no_questions_when_the_model_returns_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="atlas.retrieval_selftest"):
        questions = await _writer(StubResponses(parsed=None)).write(["one"])

    assert questions == [None]
    assert "returned nothing" in caplog.text


class StubStore:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self._chunks = chunks
        self.asked_for: list[str] = []

    def get_document_chunks(self, document_id: str) -> list[RetrievedChunk]:
        self.asked_for.append(document_id)
        return self._chunks


class StubWriter:
    def __init__(self, questions: list[str | None] | None = None) -> None:
        self._questions = questions
        self.passages: list[str] = []

    async def write(self, passages: Sequence[str]) -> list[str | None]:
        self.passages = list(passages)
        if self._questions is not None:
            return self._questions
        return [f"question {number}" for number in range(len(passages))]


class StubRetriever:
    """Returns the chunks a test wires up for each question."""

    def __init__(
        self,
        answers: dict[str, list[RetrievedChunk]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._answers = answers or {}
        self._error = error
        self.questions: list[str] = []

    async def retrieve(self, question: str) -> list[RetrievedChunk]:
        self.questions.append(question)
        if self._error is not None:
            raise self._error
        return self._answers.get(question, [])


def _selftest(
    chunks: list[RetrievedChunk],
    writer: StubWriter,
    retriever: StubRetriever,
    sample_size: int = 6,
) -> RetrievalSelfTest:
    return RetrievalSelfTest(
        cast(ChromaChunkStore, StubStore(chunks)), retriever, writer, sample_size
    )


@pytest.mark.asyncio
async def test_run_counts_the_questions_that_found_their_own_passage() -> None:
    chunks = [_chunk(0), _chunk(1), _chunk(2)]
    retriever = StubRetriever(
        {
            "question 0": [_chunk(0)],
            "question 1": [_chunk(2), _chunk(1)],
            "question 2": [_chunk(0), _chunk(1)],
        }
    )

    check = await _selftest(chunks, StubWriter(), retriever).run("doc-1")

    assert check == RetrievalCheckOut(hits=2, total=3)
    assert sorted(retriever.questions) == ["question 0", "question 1", "question 2"]


@pytest.mark.asyncio
async def test_run_does_not_count_the_same_chunk_number_from_another_document() -> None:
    retriever = StubRetriever({"question 0": [_chunk(0, document_id="other-doc")]})

    check = await _selftest([_chunk(0)], StubWriter(), retriever).run("doc-1")

    assert check == RetrievalCheckOut(hits=0, total=1)


@pytest.mark.asyncio
async def test_run_only_scores_passages_that_got_a_question() -> None:
    chunks = [_chunk(0), _chunk(1)]
    retriever = StubRetriever({"only": [_chunk(1)]})

    check = await _selftest(chunks, StubWriter([None, "only"]), retriever).run("doc-1")

    assert check == RetrievalCheckOut(hits=1, total=1)
    assert retriever.questions == ["only"]


@pytest.mark.asyncio
async def test_run_writes_questions_from_a_spread_of_passages() -> None:
    chunks = [_chunk(index, text=f"{index}-" + LONG) for index in range(10)]
    writer = StubWriter()

    await _selftest(chunks, writer, StubRetriever(), sample_size=2).run("doc-1")

    assert [passage[:2] for passage in writer.passages] == ["0-", "5-"]


@pytest.mark.asyncio
async def test_run_gives_no_score_when_the_document_has_no_usable_passage() -> None:
    writer = StubWriter()

    check = await _selftest([_chunk(0, "short")], writer, StubRetriever()).run("doc-1")

    assert check is None


@pytest.mark.asyncio
async def test_run_gives_no_score_when_no_question_could_be_written() -> None:
    retriever = StubRetriever()

    check = await _selftest([_chunk(0)], StubWriter([None]), retriever).run("doc-1")

    assert check is None
    assert retriever.questions == []


@pytest.mark.asyncio
async def test_run_gives_no_score_when_retrieval_fails_on_openai(
    caplog: pytest.LogCaptureFixture,
) -> None:
    retriever = StubRetriever(error=openai.OpenAIError("down"))

    with caplog.at_level(logging.WARNING, logger="atlas.retrieval_selftest"):
        check = await _selftest([_chunk(0)], StubWriter(), retriever).run("doc-1")

    assert check is None
    assert "OpenAIError" in caplog.text
