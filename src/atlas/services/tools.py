import json
import re
from typing import Any

GET_SUPPORT_TICKET = "get_support_ticket"
LIST_SUPPORT_TICKETS = "list_support_tickets"
_TICKET_TOOLS = {GET_SUPPORT_TICKET, LIST_SUPPORT_TICKETS}
ONLY_ONE_TICKET_TOOL = json.dumps(
    {"error": "Only one ticket tool per Ask."},
    separators=(",", ":"),
)
_TICKET_NUMBERS = {"104": "T-104", "201": "T-201", "330": "T-330"}
_TICKET_MENTION = re.compile(r"\bt[\s-]*(104|201|330)\b", re.IGNORECASE)

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
            "Look up one live support ticket. "
            "ticket_id is required. Valid ids: T-104, T-201, T-330. "
            "Do not use this to list every ticket. "
            "Use this for that ticket's status, shipping, or ETA. "
            "Do not use this for handbook policies such as refund windows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Required support ticket id, for example T-104.",
                }
            },
            "required": ["ticket_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": LIST_SUPPORT_TICKETS,
        "description": (
            "List every live support ticket id, status, and summary. "
            "Use this only when the user asks for all tickets or what tickets exist. "
            "Do not use this when the user names one id such as T-104. "
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
    if ticket_id is None or (isinstance(ticket_id, str) and not ticket_id.strip()):
        return _tool_error("ticket_id is required. Use list_support_tickets for all tickets.")
    if not isinstance(ticket_id, str):
        return _tool_error("ticket_id must be a string.")
    return lookup_support_ticket(ticket_id)


def is_ticket_tool(name: str) -> bool:
    return name in _TICKET_TOOLS


def ticket_id_from_arguments(arguments_json: str) -> str:
    parsed = parse_tool_arguments(arguments_json)
    raw = parsed.get("ticket_id")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def normalize_ticket_id(raw: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", raw.casefold())
    if compact.startswith("t") and compact[1:] in _TICKET_NUMBERS:
        return _TICKET_NUMBERS[compact[1:]]
    if compact in _TICKET_NUMBERS:
        return _TICKET_NUMBERS[compact]
    return raw.strip().upper()


def ticket_ids_in_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _TICKET_MENTION.finditer(text):
        ticket_id = _TICKET_NUMBERS[match.group(1)]
        if ticket_id in seen:
            continue
        seen.add(ticket_id)
        found.append(ticket_id)
    return found


def resolve_ticket_call(
    name: str,
    arguments_json: str,
    question: str,
) -> tuple[str, str]:
    """If the question names one ticket, always look that id up. Not the full list."""
    ids = ticket_ids_in_text(question)
    if len(ids) != 1 or not is_ticket_tool(name):
        return name, arguments_json
    ticket_id = ids[0]
    if name == LIST_SUPPORT_TICKETS or not ticket_id_from_arguments(arguments_json):
        return GET_SUPPORT_TICKET, json.dumps({"ticket_id": ticket_id})
    return GET_SUPPORT_TICKET, json.dumps(
        {"ticket_id": normalize_ticket_id(ticket_id_from_arguments(arguments_json)) or ticket_id}
    )


def order_tool_calls(calls: list[Any]) -> list[Any]:
    """Run a one-id lookup before a full list so mixed Asks keep one real ticket tool."""

    def sort_key(call: Any) -> int:
        name = str(getattr(call, "name", "") or "")
        arguments = str(getattr(call, "arguments", "") or "{}")
        if name == GET_SUPPORT_TICKET and ticket_id_from_arguments(arguments):
            return 0
        if is_ticket_tool(name):
            return 1
        return 2

    return sorted(calls, key=sort_key)


def list_support_tickets() -> str:
    tickets = [
        {"ticket_id": ticket_id, **record}
        for ticket_id, record in SUPPORT_TICKETS.items()
    ]
    return json.dumps({"tickets": tickets}, separators=(",", ":"))


def lookup_support_ticket(ticket_id: str) -> str:
    normalized = normalize_ticket_id(ticket_id)
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
