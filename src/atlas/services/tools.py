import json
from typing import Any

GET_SUPPORT_TICKET = "get_support_ticket"
LIST_SUPPORT_TICKETS = "list_support_tickets"

SUPPORT_TICKETS: dict[str, dict[str, str]] = {
    "T-104": {
        "status": "in_transit",
        "summary": "Replacement sample hardware is in transit. ETA Thursday.",
        "last_update": "2026-09-15",
    },
    "T-201": {
        "status": "refund_issued",
        "summary": "Refund processed as store credit only. No cash payout.",
        "last_update": "2026-09-12",
    },
    "T-330": {
        "status": "awaiting_photo",
        "summary": "Waiting for a photo of unopened packaging before the return can proceed.",
        "last_update": "2026-09-14",
    },
}

ATLAS_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": GET_SUPPORT_TICKET,
        "description": (
            "Look up live support tickets. "
            "Pass ticket_id such as T-104 for one ticket. "
            "Omit ticket_id to list every ticket. "
            "Use this for ticket status, shipping, or ETA. "
            "Do not use this for handbook policies such as refund windows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": (
                        "Optional support ticket id, for example T-104. "
                        "Leave out to list all tickets."
                    ),
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": LIST_SUPPORT_TICKETS,
        "description": (
            "List every live support ticket id, status, and summary. "
            "Use this when the user asks for all tickets or what tickets exist. "
            "Do not use this for handbook policies."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def run_allowlisted_tool(name: str, arguments_json: str) -> str:
    """Execute one model-proposed tool. Unknown names are rejected."""
    if name == LIST_SUPPORT_TICKETS:
        return list_support_tickets()
    if name != GET_SUPPORT_TICKET:
        return _tool_error(f"Unknown tool '{name}'.")
    try:
        payload: object = json.loads(arguments_json)
    except json.JSONDecodeError:
        return _tool_error("Tool arguments were not valid JSON.")
    if not isinstance(payload, dict):
        return _tool_error("Tool arguments must be a JSON object.")
    ticket_id = payload.get("ticket_id")
    if ticket_id is None:
        return list_support_tickets()
    if not isinstance(ticket_id, str):
        return _tool_error("ticket_id must be a string.")
    if not ticket_id.strip():
        return list_support_tickets()
    return lookup_support_ticket(ticket_id)


def list_support_tickets() -> str:
    tickets = [
        {"ticket_id": ticket_id, **record}
        for ticket_id, record in SUPPORT_TICKETS.items()
    ]
    return json.dumps({"tickets": tickets}, separators=(",", ":"))


def lookup_support_ticket(ticket_id: str) -> str:
    normalized = ticket_id.strip().upper()
    record = SUPPORT_TICKETS.get(normalized)
    if record is None:
        return _tool_error(f"No support ticket named {normalized}.")
    return json.dumps({"ticket_id": normalized, **record}, separators=(",", ":"))


def parse_tool_arguments(arguments_json: str) -> dict[str, Any]:
    try:
        payload: object = json.loads(arguments_json)
    except json.JSONDecodeError:
        return {"raw": arguments_json}
    if isinstance(payload, dict):
        return payload
    return {"raw": arguments_json}


def _tool_error(message: str) -> str:
    return json.dumps({"error": message}, separators=(",", ":"))
