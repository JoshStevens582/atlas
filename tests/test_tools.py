import json

from atlas.services.tools import (
    GET_SUPPORT_TICKET,
    LIST_SUPPORT_TICKETS,
    lookup_support_ticket,
    order_tool_calls,
    parse_tool_arguments,
    resolve_ticket_call,
    run_allowlisted_tool,
    ticket_ids_in_text,
)


class _ToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


def test_known_ticket_returns_live_status() -> None:
    payload = json.loads(lookup_support_ticket("t-104"))
    assert payload["ticket_id"] == "T-104"
    assert payload["status"] == "in_transit"
    assert "Thursday" in payload["summary"]


def test_unknown_ticket_returns_error() -> None:
    payload = json.loads(lookup_support_ticket("T-999"))
    assert "error" in payload
    assert "T-999" in payload["error"]


def test_allowlist_rejects_unknown_tool() -> None:
    payload = json.loads(run_allowlisted_tool("drop_database", "{}"))
    assert payload["error"] == "Unknown tool 'drop_database'."


def test_allowlist_rejects_invalid_json() -> None:
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, "not-json"))
    assert "valid JSON" in payload["error"]


def test_allowlist_rejects_non_object_payload() -> None:
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, "[1]"))
    assert "JSON object" in payload["error"]


def test_missing_ticket_id_is_rejected() -> None:
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, "{}"))
    assert "ticket_id is required" in payload["error"]


def test_non_string_ticket_id_is_rejected() -> None:
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, '{"ticket_id":104}'))
    assert "string" in payload["error"]
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, '{"ticket_id":"  "}'))
    assert "ticket_id is required" in payload["error"]


def test_run_tool_looks_up_ticket() -> None:
    payload = json.loads(
        run_allowlisted_tool(GET_SUPPORT_TICKET, '{"ticket_id":"T-201"}')
    )
    assert payload["status"] == "refund_issued"
    assert "store credit" in payload["summary"]


def test_ticket_ids_in_text_reads_t_330() -> None:
    assert ticket_ids_in_text("does t 330 involve a refund") == ["T-330"]
    assert ticket_ids_in_text("Does T-104 get a 14-day refund?") == ["T-104"]
    assert ticket_ids_in_text("List all support tickets") == []


def test_resolve_ticket_call_turns_list_into_one_id_lookup() -> None:
    name, arguments = resolve_ticket_call(
        LIST_SUPPORT_TICKETS,
        "{}",
        "does t 330 involve a refund",
    )
    assert name == GET_SUPPORT_TICKET
    assert json.loads(arguments)["ticket_id"] == "T-330"


def test_resolve_ticket_call_keeps_list_when_no_id() -> None:
    name, arguments = resolve_ticket_call(
        LIST_SUPPORT_TICKETS,
        "{}",
        "List all support tickets",
    )
    assert name == LIST_SUPPORT_TICKETS
    assert arguments == "{}"


def test_order_tool_calls_runs_one_id_lookup_first() -> None:
    listed = _ToolCall(LIST_SUPPORT_TICKETS, "{}")
    got = _ToolCall(GET_SUPPORT_TICKET, '{"ticket_id":"T-104"}')
    ordered = order_tool_calls([listed, got])
    assert [call.name for call in ordered] == [
        GET_SUPPORT_TICKET,
        LIST_SUPPORT_TICKETS,
    ]


def test_list_support_tickets_tool() -> None:
    payload = json.loads(run_allowlisted_tool(LIST_SUPPORT_TICKETS, "{}"))
    ids = {item["ticket_id"] for item in payload["tickets"]}
    assert ids == {"T-104", "T-201", "T-330"}


def test_parse_tool_arguments_object() -> None:
    assert parse_tool_arguments('{"ticket_id":"T-104"}') == {"ticket_id": "T-104"}


def test_parse_tool_arguments_invalid_json() -> None:
    assert parse_tool_arguments("{") == {"raw": "{"}


def test_parse_tool_arguments_non_object() -> None:
    assert parse_tool_arguments("[1]") == {"raw": "[1]"}
