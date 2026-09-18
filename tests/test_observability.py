from types import SimpleNamespace

from atlas.schemas.chat import RetrievedChunk
from atlas.services.observability import AskTrace, safe_log_fields


def test_safe_log_fields_strips_secrets_and_bodies() -> None:
    cleaned = safe_log_fields(
        {
            "request_id": "abc",
            "password": "nope",
            "api_key": "sk-secret",
            "question": "full text",
            "prompt": "rules + docs",
            "chunk_ids": ["doc:0"],
            "Authorization": "Bearer tok",
        }
    )
    assert cleaned == {"request_id": "abc", "chunk_ids": ["doc:0"]}
    assert "password" not in cleaned
    assert "api_key" not in cleaned
    assert "question" not in cleaned


def test_ask_trace_records_retrieve_tools_and_usage() -> None:
    trace = AskTrace(owner_id="alice", model="gpt-4o-mini")
    trace.record_question_length("What is the project codename?")
    trace.record_thread("thread-1")
    trace.record_retrieve(
        [
            RetrievedChunk(
                document_id="doc-a",
                document_title="Demo Note",
                chunk_index=0,
                text="codename Northstar",
                distance=0.4,
            )
        ]
    )
    trace.record_tool("list_support_tickets")
    trace.record_usage(
        SimpleNamespace(usage=SimpleNamespace(input_tokens=12, output_tokens=8))
    )
    trace.complete("The project codename is Northstar.")

    assert trace.request_id
    assert trace.owner_id == "alice"
    assert trace.thread_id == "thread-1"
    assert trace.chunk_ids == ["doc-a:0"]
    assert trace.chunk_titles == ["Demo Note"]
    assert trace.tool_names == ["list_support_tickets"]
    assert trace.input_tokens == 12
    assert trace.output_tokens == 8
    assert trace.question_chars == len("What is the project codename?")
    assert trace.answer_chars == len("The project codename is Northstar.")
    assert trace.error is None


def test_ask_trace_records_error_class_not_message() -> None:
    trace = AskTrace(owner_id="bob", model="gpt-4o-mini")
    try:
        raise ValueError("user typed a secret password=hunter2")
    except ValueError as exc:
        trace.record_error(exc)
    trace.complete("")
    assert trace.error == "ValueError"
