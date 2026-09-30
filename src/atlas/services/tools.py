"""Tools the chat model may call. Both do exact calculations the handbook text
cannot do: the rules live in the handbook (retrieved), the arithmetic lives here.
"""

import json
import math
from datetime import date, timedelta
from typing import Any

ESTIMATE_ANNUAL_LEAVE = "estimate_annual_leave"
GET_FEDERAL_HOLIDAYS = "get_federal_holidays"

# From the handbook leave page: accrual tiers by years of federal service.
ANNUAL_LEAVE_CARRYOVER_CAP_HOURS = 240
MAX_PAY_PERIODS_PER_YEAR = 26
MAX_YEARS_OF_SERVICE = 60
MAX_LEAVE_HOURS = 2000
# Juneteenth became a federal holiday in 2021, so earlier years would be wrong.
FIRST_HOLIDAY_YEAR = 2021
LAST_HOLIDAY_YEAR = 2100

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONDAY = 0
_THURSDAY = 3
_SATURDAY = 5
_SUNDAY = 6

class ToolInputError(ValueError):
    """The model sent arguments a tool cannot use. The message goes back to it."""


ATLAS_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": ESTIMATE_ANNUAL_LEAVE,
        "description": (
            "Project a person's annual leave hours at the end of the leave year and "
            "how many hours they would lose to the carry-over cap (use or lose). "
            "Use this whenever the user gives their own numbers (current balance, "
            "years of federal service, pay periods left) and wants a total. "
            "Never do this arithmetic yourself. "
            "Do not use this to explain the leave rules; those are in the handbook."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "current_hours": {
                    "type": "number",
                    "description": "Annual leave hours available right now.",
                },
                "years_of_service": {
                    "type": "number",
                    "description": "Years of federal service. Sets the accrual rate.",
                },
                "pay_periods_remaining": {
                    "type": "integer",
                    "description": "Pay periods left in the leave year (0 to 26).",
                },
            },
            "required": ["current_hours", "years_of_service", "pay_periods_remaining"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": GET_FEDERAL_HOLIDAYS,
        "description": (
            "List the federal holidays for one calendar year with their dates, the "
            "weekday, and the day they are observed when they fall on a weekend. "
            "Use this for any question about which day a holiday falls on. "
            "Do not use this for holiday pay rules; those are in the handbook."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": f"Calendar year, {FIRST_HOLIDAY_YEAR} to {LAST_HOLIDAY_YEAR}.",
                }
            },
            "required": ["year"],
            "additionalProperties": False,
        },
    },
]


def run_allowlisted_tool(name: str, arguments_json: str) -> str:
    """Execute one model-proposed tool. Unknown names are rejected."""
    if name not in {ESTIMATE_ANNUAL_LEAVE, GET_FEDERAL_HOLIDAYS}:
        return _tool_error(f"Unknown tool '{name}'.")
    try:
        payload: object = json.loads(arguments_json)
    except json.JSONDecodeError:
        return _tool_error("Tool arguments were not valid JSON.")
    if not isinstance(payload, dict):
        return _tool_error("Tool arguments must be a JSON object.")
    try:
        if name == GET_FEDERAL_HOLIDAYS:
            return _run_get_federal_holidays(payload)
        return _run_estimate_annual_leave(payload)
    except ToolInputError as exc:
        return _tool_error(str(exc))


def parse_tool_arguments(arguments_json: str) -> dict[str, Any]:
    try:
        payload: object = json.loads(arguments_json)
    except json.JSONDecodeError:
        return {"raw": arguments_json}
    if isinstance(payload, dict):
        return payload
    return {"raw": arguments_json}


def annual_leave_hours_per_pay_period(years_of_service: float) -> int:
    """Handbook tiers: under 3 years 4, from 3 up to 15 years 6, 15 or more 8."""
    if years_of_service < 3:
        return 4
    if years_of_service < 15:
        return 6
    return 8


def federal_holidays(year: int) -> list[dict[str, str]]:
    """The eleven federal holidays (5 U.S.C. 6103) for ``year``.

    Inauguration Day is left out: it only applies in the Washington, DC area.
    """
    holidays: list[tuple[str, date]] = [
        ("New Year's Day", date(year, 1, 1)),
        ("Birthday of Martin Luther King, Jr.", _nth_weekday(year, 1, _MONDAY, 3)),
        ("Washington's Birthday", _nth_weekday(year, 2, _MONDAY, 3)),
        ("Memorial Day", _last_weekday(year, 5, _MONDAY)),
        ("Juneteenth National Independence Day", date(year, 6, 19)),
        ("Independence Day", date(year, 7, 4)),
        ("Labor Day", _nth_weekday(year, 9, _MONDAY, 1)),
        ("Columbus Day", _nth_weekday(year, 10, _MONDAY, 2)),
        ("Veterans Day", date(year, 11, 11)),
        ("Thanksgiving Day", _nth_weekday(year, 11, _THURSDAY, 4)),
        ("Christmas Day", date(year, 12, 25)),
    ]
    return [
        {
            "name": name,
            "date": day.isoformat(),
            "weekday": _WEEKDAYS[day.weekday()],
            "observed": _observed(day).isoformat(),
        }
        for name, day in holidays
    ]


def estimate_annual_leave(
    current_hours: float,
    years_of_service: float,
    pay_periods_remaining: int,
) -> dict[str, Any]:
    rate = annual_leave_hours_per_pay_period(years_of_service)
    accrued = rate * pay_periods_remaining
    projected = current_hours + accrued
    return {
        "hours_per_pay_period": rate,
        "hours_accrued": accrued,
        "projected_hours": round(projected, 2),
        "carryover_cap_hours": ANNUAL_LEAVE_CARRYOVER_CAP_HOURS,
        "use_or_lose_hours": round(max(0.0, projected - ANNUAL_LEAVE_CARRYOVER_CAP_HOURS), 2),
        "assumption": (
            "Uses one accrual rate for every remaining pay period. If a work "
            "anniversary falls before the end of the leave year, the rate may step up."
        ),
    }


def _run_estimate_annual_leave(payload: dict[str, Any]) -> str:
    current_hours = _read_number(payload, "current_hours", 0, MAX_LEAVE_HOURS)
    years = _read_number(payload, "years_of_service", 0, MAX_YEARS_OF_SERVICE)
    periods = _read_whole_number(payload, "pay_periods_remaining", 0, MAX_PAY_PERIODS_PER_YEAR)
    return json.dumps(
        estimate_annual_leave(current_hours, years, periods),
        separators=(",", ":"),
    )


def _run_get_federal_holidays(payload: dict[str, Any]) -> str:
    year = _read_whole_number(payload, "year", FIRST_HOLIDAY_YEAR, LAST_HOLIDAY_YEAR)
    return json.dumps(
        {
            "year": year,
            "holidays": federal_holidays(year),
            "note": (
                "Observed is the day off when the holiday falls on a Saturday (Friday) "
                "or Sunday (Monday). An agency can move a holiday for people whose "
                "schedule has that day off."
            ),
        },
        separators=(",", ":"),
    )


def _read_number(payload: dict[str, Any], key: str, low: float, high: float) -> float:
    """A number in range. Booleans and NaN are not numbers here."""
    if key not in payload or payload[key] is None:
        raise ToolInputError(f"{key} is required.")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ToolInputError(f"{key} must be a number.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolInputError(f"{key} must be a finite number.")
    if value < low or value > high:
        raise ToolInputError(f"{key} must be between {low:g} and {high:g}.")
    return float(value)


def _read_whole_number(payload: dict[str, Any], key: str, low: int, high: int) -> int:
    value = _read_number(payload, key, low, high)
    if not value.is_integer():
        raise ToolInputError(f"{key} must be a whole number.")
    return int(value)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    following_month = date(year + (month == 12), month % 12 + 1, 1)
    last_day = following_month - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == _SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == _SUNDAY:
        return day + timedelta(days=1)
    return day


def _tool_error(message: str) -> str:
    return json.dumps({"error": message}, separators=(",", ":"))
