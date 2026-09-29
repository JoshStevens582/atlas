import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from atlas.services import observability
from atlas.services.observability import AskTrace


def _trace() -> AskTrace:
    return AskTrace(owner_id="alice", model="test-model")


def _response(**usage: Any) -> SimpleNamespace:
    return SimpleNamespace(usage=SimpleNamespace(**usage))


def test_a_trace_line_that_cannot_be_written_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the log folder should be", encoding="utf-8")
    monkeypatch.setattr(observability, "_TRACE_LOG_PATH", blocker / "ask_traces.log")

    with caplog.at_level(logging.ERROR, logger="atlas.ask"):
        observability._write_trace_line("ask_retrieve {}")

    assert "failed to write ask trace log" in caplog.text


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
