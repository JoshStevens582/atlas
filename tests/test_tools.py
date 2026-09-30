import json
from datetime import date
from typing import Any

import pytest

from atlas.services.tools import (
    ANNUAL_LEAVE_CARRYOVER_CAP_HOURS,
    ATLAS_TOOLS,
    ESTIMATE_ANNUAL_LEAVE,
    GET_FEDERAL_HOLIDAYS,
    _last_weekday,
    annual_leave_hours_per_pay_period,
    estimate_annual_leave,
    federal_holidays,
    parse_tool_arguments,
    run_allowlisted_tool,
)


def _run(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(run_allowlisted_tool(name, json.dumps(arguments)))
    return result


def _holidays_by_name(year: int) -> dict[str, dict[str, str]]:
    return {holiday["name"]: holiday for holiday in federal_holidays(year)}


def test_the_model_is_offered_exactly_the_two_handbook_tools() -> None:
    assert [tool["name"] for tool in ATLAS_TOOLS] == [ESTIMATE_ANNUAL_LEAVE, GET_FEDERAL_HOLIDAYS]
    for tool in ATLAS_TOOLS:
        assert tool["parameters"]["additionalProperties"] is False
        assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])


@pytest.mark.parametrize(
    ("years", "hours"),
    [(0, 4), (2.99, 4), (3, 6), (14.99, 6), (15, 8), (40, 8)],
)
def test_annual_leave_rate_follows_the_handbook_tiers(years: float, hours: int) -> None:
    assert annual_leave_hours_per_pay_period(years) == hours


def test_estimate_projects_hours_and_the_use_or_lose_amount() -> None:
    result = estimate_annual_leave(
        current_hours=182, years_of_service=5, pay_periods_remaining=25
    )

    assert result["hours_per_pay_period"] == 6
    assert result["hours_accrued"] == 150
    assert result["projected_hours"] == 332
    assert result["carryover_cap_hours"] == ANNUAL_LEAVE_CARRYOVER_CAP_HOURS == 240
    assert result["use_or_lose_hours"] == 92
    assert "anniversary" in result["assumption"]


def test_estimate_loses_nothing_when_the_projection_is_under_the_cap() -> None:
    result = estimate_annual_leave(80, 1, 10)

    assert result["projected_hours"] == 120
    assert result["use_or_lose_hours"] == 0


def test_estimate_keeps_half_hours() -> None:
    assert estimate_annual_leave(100.25, 1, 0)["projected_hours"] == 100.25


def test_estimate_tool_runs_from_model_arguments() -> None:
    payload = _run(
        ESTIMATE_ANNUAL_LEAVE,
        {"current_hours": 200, "years_of_service": 16, "pay_periods_remaining": 10},
    )

    assert payload["hours_per_pay_period"] == 8
    assert payload["projected_hours"] == 280
    assert payload["use_or_lose_hours"] == 40


def test_estimate_tool_accepts_a_whole_number_sent_as_a_float() -> None:
    payload = _run(
        ESTIMATE_ANNUAL_LEAVE,
        {"current_hours": 10, "years_of_service": 1, "pay_periods_remaining": 26.0},
    )

    assert payload["hours_accrued"] == 104


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "current_hours is required."),
        (
            {"current_hours": None, "years_of_service": 1, "pay_periods_remaining": 1},
            "current_hours is required.",
        ),
        (
            {"current_hours": "lots", "years_of_service": 1, "pay_periods_remaining": 1},
            "current_hours must be a number.",
        ),
        (
            {"current_hours": True, "years_of_service": 1, "pay_periods_remaining": 1},
            "current_hours must be a number.",
        ),
        (
            {"current_hours": -1, "years_of_service": 1, "pay_periods_remaining": 1},
            "current_hours must be between 0 and 2000.",
        ),
        (
            {"current_hours": 1, "years_of_service": 61, "pay_periods_remaining": 1},
            "years_of_service must be between 0 and 60.",
        ),
        (
            {"current_hours": 1, "years_of_service": 1, "pay_periods_remaining": 27},
            "pay_periods_remaining must be between 0 and 26.",
        ),
        (
            {"current_hours": 1, "years_of_service": 1, "pay_periods_remaining": 2.5},
            "pay_periods_remaining must be a whole number.",
        ),
        (
            {"current_hours": 1, "years_of_service": 1, "pay_periods_remaining": 10**400},
            "pay_periods_remaining must be between 0 and 26.",
        ),
    ],
)
def test_estimate_tool_rejects_bad_arguments_with_a_clear_message(
    arguments: dict[str, Any], message: str
) -> None:
    assert _run(ESTIMATE_ANNUAL_LEAVE, arguments) == {"error": message}


def test_estimate_tool_rejects_not_a_number() -> None:
    raw = '{"current_hours": NaN, "years_of_service": 1, "pay_periods_remaining": 1}'

    payload = json.loads(run_allowlisted_tool(ESTIMATE_ANNUAL_LEAVE, raw))

    assert payload == {"error": "current_hours must be a finite number."}


def test_federal_holidays_for_2026_match_the_calendar() -> None:
    holidays = _holidays_by_name(2026)

    assert len(holidays) == 11
    assert holidays["New Year's Day"]["date"] == "2026-01-01"
    assert holidays["Birthday of Martin Luther King, Jr."]["date"] == "2026-01-19"
    assert holidays["Washington's Birthday"]["date"] == "2026-02-16"
    assert holidays["Memorial Day"]["date"] == "2026-05-25"
    assert holidays["Juneteenth National Independence Day"]["date"] == "2026-06-19"
    assert holidays["Independence Day"]["date"] == "2026-07-04"
    assert holidays["Labor Day"]["date"] == "2026-09-07"
    assert holidays["Columbus Day"]["date"] == "2026-10-12"
    assert holidays["Veterans Day"]["date"] == "2026-11-11"
    assert holidays["Thanksgiving Day"]["date"] == "2026-11-26"
    assert holidays["Christmas Day"]["date"] == "2026-12-25"


def test_federal_holidays_name_the_weekday() -> None:
    holidays = _holidays_by_name(2026)

    assert holidays["Thanksgiving Day"]["weekday"] == "Thursday"
    assert holidays["Memorial Day"]["weekday"] == "Monday"
    assert holidays["Independence Day"]["weekday"] == "Saturday"


def test_a_saturday_holiday_is_observed_on_the_friday_before() -> None:
    july_fourth = _holidays_by_name(2026)["Independence Day"]

    assert july_fourth["date"] == "2026-07-04"
    assert july_fourth["observed"] == "2026-07-03"


def test_a_sunday_holiday_is_observed_on_the_monday_after() -> None:
    july_fourth = _holidays_by_name(2027)["Independence Day"]

    assert july_fourth["weekday"] == "Sunday"
    assert july_fourth["observed"] == "2027-07-05"


def test_a_saturday_new_year_is_observed_in_the_year_before() -> None:
    assert _holidays_by_name(2022)["New Year's Day"]["observed"] == "2021-12-31"


def test_a_weekday_holiday_is_observed_on_its_own_date() -> None:
    thanksgiving = _holidays_by_name(2026)["Thanksgiving Day"]

    assert thanksgiving["observed"] == thanksgiving["date"]


def test_last_weekday_finds_the_last_monday_of_december() -> None:
    assert _last_weekday(2026, 12, 0) == date(2026, 12, 28)


def test_federal_holidays_tool_returns_the_year_and_a_note() -> None:
    payload = _run(GET_FEDERAL_HOLIDAYS, {"year": 2026})

    assert payload["year"] == 2026
    assert len(payload["holidays"]) == 11
    assert "Saturday" in payload["note"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "year is required."),
        ({"year": "2026"}, "year must be a number."),
        ({"year": 2020}, "year must be between 2021 and 2100."),
        ({"year": 2101}, "year must be between 2021 and 2100."),
        ({"year": 2026.5}, "year must be a whole number."),
    ],
)
def test_federal_holidays_tool_rejects_bad_years(
    arguments: dict[str, Any], message: str
) -> None:
    assert _run(GET_FEDERAL_HOLIDAYS, arguments) == {"error": message}


def test_allowlist_rejects_unknown_tool() -> None:
    payload = json.loads(run_allowlisted_tool("drop_database", "{}"))

    assert payload == {"error": "Unknown tool 'drop_database'."}


def test_allowlist_rejects_old_ticket_tools() -> None:
    payload = json.loads(run_allowlisted_tool("get_support_ticket", '{"ticket_id": "T-104"}'))

    assert payload["error"].startswith("Unknown tool")


@pytest.mark.parametrize("tool_name", [ESTIMATE_ANNUAL_LEAVE, GET_FEDERAL_HOLIDAYS])
def test_allowlist_rejects_invalid_json(tool_name: str) -> None:
    payload = json.loads(run_allowlisted_tool(tool_name, "not-json"))

    assert payload == {"error": "Tool arguments were not valid JSON."}


@pytest.mark.parametrize("tool_name", [ESTIMATE_ANNUAL_LEAVE, GET_FEDERAL_HOLIDAYS])
def test_allowlist_rejects_arguments_that_are_not_an_object(tool_name: str) -> None:
    payload = json.loads(run_allowlisted_tool(tool_name, "[1, 2]"))

    assert payload == {"error": "Tool arguments must be a JSON object."}


def test_parse_tool_arguments_returns_an_object_or_wraps_the_raw_text() -> None:
    assert parse_tool_arguments('{"year": 2026}') == {"year": 2026}
    assert parse_tool_arguments("not-json") == {"raw": "not-json"}
    assert parse_tool_arguments("[1]") == {"raw": "[1]"}
