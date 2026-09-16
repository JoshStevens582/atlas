import json

from atlas.services.tools import (
    GET_SUPPORT_TICKET,
    LIST_SUPPORT_TICKETS,
    lookup_support_ticket,
    parse_tool_arguments,
    run_allowlisted_tool,
)


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


def test_missing_ticket_id_lists_all_tickets() -> None:
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, "{}"))
    ids = {item["ticket_id"] for item in payload["tickets"]}
    assert ids == {"T-104", "T-201", "T-330"}


def test_non_string_ticket_id_is_rejected() -> None:
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, '{"ticket_id":104}'))
    assert "string" in payload["error"]
    payload = json.loads(run_allowlisted_tool(GET_SUPPORT_TICKET, '{"ticket_id":"  "}'))
    assert len(payload["tickets"]) == 3


def test_run_tool_looks_up_ticket() -> None:
    payload = json.loads(
        run_allowlisted_tool(GET_SUPPORT_TICKET, '{"ticket_id":"T-201"}')
    )
    assert payload["status"] == "refund_issued"
    assert "store credit" in payload["summary"]


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
