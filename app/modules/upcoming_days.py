"""Upcoming Days: the next few dates and their arts, food and fun national days.

A week-ahead companion to the National Days module. For each of the next few
dates (starting tomorrow) it prints a bold date and the names of that date's
arts & entertainment, food & beverage and fun days, read live from
nationaltoday.com. A date with none of those is left out.

All the fetching, parsing and caching lives in national_days.py (see
load_upcoming_days there), so the same politeness rules apply: only
nationaltoday.com, sequential requests, a time budget, and nothing stored in the
repository. Run scripts/check_national_days.py --upcoming 7 to see what the
parsers make of the live pages.
"""

from datetime import date
from typing import Any, Dict, List

from app.module_registry import register_module
from app.modules import national_days as nd
from app.modules.history import _resolve_reference_date

MIN_DATES = 3  # dates listed
DEFAULT_DATES = 7  # a week ahead
MAX_DATES = 7
DEFAULT_PER_DATE = 2  # days listed under each date
MAX_PER_DATE = 5
ICON = "calendar-star"

# Only the categories this module lists get a "Skip" box.
SKIPPABLE = [(label, key, title) for label, key, title in nd.CATEGORIES if label in nd.UPCOMING_CATEGORIES]


def _heading(day: date) -> str:
    """The bold line above each date's days, e.g. "Fri, Nov 20"."""
    return f"{day:%a}, {day:%b} {day.day}"


def _range_caption(groups: List[Dict[str, Any]]) -> str:
    """The span the list covers, e.g. "Nov 20 - Nov 26, 2026"."""
    first, last = groups[0]["date"], groups[-1]["date"]
    if first == last:
        return f"{first:%b} {first.day}, {first.year}"
    return f"{first:%b} {first.day} - {last:%b} {last.day}, {last.year}"


@register_module(
    type_id="upcoming_days",
    label="Upcoming Days",
    description="The next few dates' arts, food and fun national days (live from nationaltoday.com)",
    icon=ICON,
    offline=False,
    category="content",
    config_schema={
        "type": "object",
        "description": (
            "Lists the next few dates, starting tomorrow, with their arts, food and fun national "
            "days. A date with none is left out. Tick a \"Skip\" box to leave a kind of day out."
        ),
        "properties": {
            "count": {
                "type": "integer",
                "title": "Dates to List",
                "description": "How many upcoming dates to list. Set 7 for a week ahead.",
                "default": DEFAULT_DATES,
                "minimum": MIN_DATES,
                "maximum": MAX_DATES,
            },
            "per_day": {
                "type": "integer",
                "title": "Days per Date",
                "description": "The most national days listed under each date (arts first, then food, then fun).",
                "default": DEFAULT_PER_DATE,
                "minimum": 1,
                "maximum": MAX_PER_DATE,
            },
            **{key: {"type": "boolean", "title": title, "default": False} for _label, key, title in SKIPPABLE},
        },
    },
)
def format_upcoming_days_receipt(printer, config: Dict[str, Any] = None, module_name: str = None):
    """Prints the next few dates, each with its arts, food and fun national days."""

    config = config or {}
    today = _resolve_reference_date(config)
    dates = nd._resolve_int(config, "count", DEFAULT_DATES, MAX_DATES, MIN_DATES)
    per_date = nd._resolve_int(config, "per_day", DEFAULT_PER_DATE, MAX_PER_DATE)

    printer.print_header(module_name or "UPCOMING DAYS", icon=ICON)
    groups = nd.load_upcoming_days(today, dates, per_date, nd._skipped_categories(config))
    if groups is None:
        printer.print_line()
        printer.print_body("Couldn't load upcoming")
        printer.print_body("days. Check the internet")
        printer.print_body("connection.")
        return
    if not groups:
        printer.print_line()
        printer.print_body("Nothing matching in the")
        printer.print_body(f"next {nd.upcoming_lookahead(dates)} days.")
        return

    printer.print_caption(_range_caption(groups))
    for group in groups:
        printer.print_line()  # a rule above every date, so the dates stand out at a glance
        printer.print_bold(_heading(group["date"]))
        for holiday in group["holidays"]:
            printer.print_body(holiday["name"])
    printer.print_line()
    printer.print_caption(nd.SOURCE_CREDIT)
