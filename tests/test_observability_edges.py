import logging
from types import SimpleNamespace
from typing import Any

import pytest

from atlas.services.observability import AskTrace


def _trace() -> AskTrace:
    return AskTrace(owner_id="alice", model="test-model")


def _response(**usage: Any) -> SimpleNamespace:
    return SimpleNamespace(usage=SimpleNamespace(**usage))


def test_emit_logs_structured_event_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trace = _trace()
    with caplog.at_level(logging.INFO, logger="atlas.ask"):
        trace.record_retrieve([])

    assert "ask_retrieve" in caplog.text


def test_a_response_without_usage_changes_nothing() -> None:
    trace = _trace()

    trace.record_usage(SimpleNamespace())

    assert (trace.input_tokens, trace.output_tokens) == (None, None)


def test_usage_named_input_and_output_tokens_is_added_up_across_calls() -> None:
    trace = _trace()

    trace.record_usage(_response(input_tokens=10, output_tokens=4))
    trace.record_usage(_response(input_tokens=5, output_tokens=1))

    assert (trace.input_tokens, trace.output_tokens) == (15, 5)


def test_usage_named_prompt_and_completion_tokens_is_understood_too() -> None:
    trace = _trace()

    trace.record_usage(_response(prompt_tokens=7, completion_tokens=3))

    assert (trace.input_tokens, trace.output_tokens) == (7, 3)


def test_usage_values_that_are_not_whole_numbers_are_ignored() -> None:
    trace = _trace()

    trace.record_usage(_response(input_tokens="lots", output_tokens=None))

    assert (trace.input_tokens, trace.output_tokens) == (None, None)


def test_usage_with_only_one_side_records_only_that_side() -> None:
    trace = _trace()

    trace.record_usage(_response(input_tokens=9))

    assert (trace.input_tokens, trace.output_tokens) == (9, None)
