import json
from types import SimpleNamespace

import pytest

from atlas.services.tools import (
    GET_SUPPORT_TICKET,
    LIST_SUPPORT_TICKETS,
    normalize_ticket_id,
    order_tool_calls,
    resolve_ticket_call,
    ticket_id_from_arguments,
    ticket_ids_in_text,
)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"ticket_id": "  T-104 "}', "T-104"),
        ('{"ticket_id": 104}', ""),
        ('{"other": "x"}', ""),
        ("not json", ""),
        ("[1, 2]", ""),
    ],
)
def test_ticket_id_from_arguments_only_trusts_a_string(arguments: str, expected: str) -> None:
    assert ticket_id_from_arguments(arguments) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("T-104", "T-104"),
        ("t 201", "T-201"),
        ("t330", "T-330"),
        ("104", "T-104"),
        ("something else", "SOMETHING ELSE"),
    ],
)
def test_normalize_ticket_id(raw: str, expected: str) -> None:
    assert normalize_ticket_id(raw) == expected


def test_ticket_ids_in_text_finds_each_known_ticket_once_in_order() -> None:
    text = "Compare T-201 with t 104, and T-201 again. T-999 is not real."

    assert ticket_ids_in_text(text) == ["T-201", "T-104"]


def test_ticket_ids_in_text_with_none_named() -> None:
    assert ticket_ids_in_text("What is the refund window?") == []


def _arguments(call: tuple[str, str]) -> dict[str, str]:
    parsed: dict[str, str] = json.loads(call[1])
    return parsed


def test_a_named_ticket_turns_the_list_tool_into_a_single_lookup() -> None:
    call = resolve_ticket_call(LIST_SUPPORT_TICKETS, "{}", "What is the status of T-201?")

    assert call[0] == GET_SUPPORT_TICKET
    assert _arguments(call) == {"ticket_id": "T-201"}


def test_a_lookup_with_no_id_gets_the_id_from_the_question() -> None:
    call = resolve_ticket_call(GET_SUPPORT_TICKET, "{}", "Where is T-104?")

    assert call[0] == GET_SUPPORT_TICKET
    assert _arguments(call) == {"ticket_id": "T-104"}


def test_a_lookup_keeps_and_tidies_the_id_the_model_chose() -> None:
    call = resolve_ticket_call(GET_SUPPORT_TICKET, '{"ticket_id": "t 330"}', "Tell me about T-330")

    assert _arguments(call) == {"ticket_id": "T-330"}


def test_two_named_tickets_leave_the_call_alone() -> None:
    arguments = '{"ticket_id": "T-104"}'

    assert resolve_ticket_call(GET_SUPPORT_TICKET, arguments, "T-104 and T-201?") == (
        GET_SUPPORT_TICKET,
        arguments,
    )


def test_no_named_ticket_leaves_the_call_alone() -> None:
    assert resolve_ticket_call(LIST_SUPPORT_TICKETS, "{}", "List every ticket") == (
        LIST_SUPPORT_TICKETS,
        "{}",
    )


def test_a_tool_that_is_not_a_ticket_tool_is_never_rewritten() -> None:
    assert resolve_ticket_call("delete_everything", "{}", "T-104?") == ("delete_everything", "{}")


def test_order_tool_calls_puts_a_single_lookup_before_the_list_and_others_last() -> None:
    other = SimpleNamespace(name="other_tool", arguments="{}")
    listing = SimpleNamespace(name=LIST_SUPPORT_TICKETS, arguments="{}")
    lookup = SimpleNamespace(name=GET_SUPPORT_TICKET, arguments='{"ticket_id": "T-104"}')
    empty_lookup = SimpleNamespace(name=GET_SUPPORT_TICKET, arguments="{}")

    ordered = order_tool_calls([other, listing, empty_lookup, lookup])

    assert ordered == [lookup, listing, empty_lookup, other]
