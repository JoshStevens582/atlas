import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import openai
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from atlas.schemas.chat import AnswerCheck, RetrievedChunk
from atlas.services.answer_check import (
    CHECK_INSTRUCTIONS,
    LlmAnswerChecker,
    build_check_payload,
)


def _chunk(index: int, text: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc",
        document_title="Handbook",
        chunk_index=index,
        text=text or f"paragraph {index}",
        distance=0.3,
    )


class StubResponses:
    def __init__(
        self,
        parsed: AnswerCheck | None = None,
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


def _checker(responses: StubResponses) -> LlmAnswerChecker:
    client = cast(AsyncOpenAI, SimpleNamespace(responses=responses))
    return LlmAnswerChecker(client, "test-check-model")


def _sources(*texts: str) -> Sequence[RetrievedChunk]:
    return [_chunk(index, text) for index, text in enumerate(texts)]


def test_build_check_payload_numbers_each_source_and_caps_length() -> None:
    payload = build_check_payload(
        "what is it?",
        "It is x. [1]",
        [_chunk(0, "short"), _chunk(1, "y" * 5000)],
    )

    assert payload.startswith("<question>\nwhat is it?\n</question>")
    assert "<answer>\nIt is x. [1]\n</answer>" in payload
    assert '<source id="1">\nshort\n</source>' in payload
    assert '<source id="2">' in payload
    assert "y" * 1200 in payload
    assert "y" * 1201 not in payload


def test_build_check_payload_caps_a_very_long_answer() -> None:
    payload = build_check_payload("q", "z" * 9000, [_chunk(0)])

    assert "z" * 4000 in payload
    assert "z" * 4001 not in payload


@pytest.mark.asyncio
async def test_check_returns_the_models_verdict_and_asks_once() -> None:
    verdict = AnswerCheck(verdict="partly_supported", reason="Only the date is in [1].")
    responses = StubResponses(verdict)

    checked = await _checker(responses).check("question", "answer [1]", _sources("text"))

    assert checked == verdict
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call["model"] == "test-check-model"
    assert call["instructions"] == CHECK_INSTRUCTIONS
    assert call["text_format"] is AnswerCheck
    assert call["temperature"] == 0
    assert "<question>\nquestion\n</question>" in call["input"]


@pytest.mark.asyncio
async def test_check_shortens_a_reason_that_is_too_long() -> None:
    responses = StubResponses(AnswerCheck(verdict="supported", reason=f"  {'r' * 900}  "))

    checked = await _checker(responses).check("question", "answer", _sources("text"))

    assert checked is not None
    assert checked.reason == "r" * 300


@pytest.mark.asyncio
async def test_check_skips_the_model_when_there_is_nothing_to_check() -> None:
    responses = StubResponses(AnswerCheck(verdict="supported", reason="ok"))
    checker = _checker(responses)

    assert await checker.check("question", "   ", _sources("text")) is None
    assert await checker.check("question", "answer", []) is None
    assert responses.calls == []


@pytest.mark.asyncio
async def test_check_gives_no_verdict_when_openai_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    checker = _checker(StubResponses(error=openai.OpenAIError("connection dropped")))

    with caplog.at_level(logging.WARNING, logger="atlas.answer_check"):
        checked = await checker.check("question", "answer", _sources("text"))

    assert checked is None
    assert "OpenAIError" in caplog.text
    assert "connection dropped" not in caplog.text


@pytest.mark.asyncio
async def test_check_gives_no_verdict_when_the_reply_does_not_parse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ValidationError) as caught:
        AnswerCheck.model_validate({"verdict": "great", "reason": "x"})
    checker = _checker(StubResponses(error=caught.value))

    with caplog.at_level(logging.WARNING, logger="atlas.answer_check"):
        checked = await checker.check("question", "answer", _sources("text"))

    assert checked is None
    assert "ValidationError" in caplog.text


@pytest.mark.asyncio
async def test_check_gives_no_verdict_when_the_model_returns_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    checker = _checker(StubResponses(parsed=None))

    with caplog.at_level(logging.WARNING, logger="atlas.answer_check"):
        checked = await checker.check("question", "answer", _sources("text"))

    assert checked is None
    assert "no verdict" in caplog.text
