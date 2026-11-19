"""Famous birthdays for today's date, from a bundled offline list.

The data file is built by scripts/build_history_data.py (from Wikidata) and kept
separate from the "On This Day" history so a handful of birthdays never crowd
out that module's film, tech and gaming milestones.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from app.module_registry import register_module
from app.modules.history import _resolve_reference_date, draw_timeline_image

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Shape: {"births": {month: {day: [person]}}} where
# person = {"y": birth year, "n": name, "o": occupation, "s": Wikipedia language editions}.
BIRTHDAYS_DB_PATH = DATA_DIR / "birthdays.json"

DEFAULT_COUNT = 5
MAX_COUNT = 20

_cache: Dict[str, Any] = {"key": None, "data": None}


def _load_births_db() -> Dict[str, Any] | None:
    """Load month -> day -> people, cached by file mtime.

    Returns None when the file is missing or unreadable so the receipt can say
    there is nothing to show instead of failing.
    """
    path = BIRTHDAYS_DB_PATH
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None

    if _cache["key"] == key:
        return _cache["data"]

    data = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        births = raw.get("births") if isinstance(raw, dict) else None
        if isinstance(births, dict):
            data = births
    except (OSError, ValueError):
        data = None

    _cache["key"] = key
    _cache["data"] = data
    return data


def get_birthdays_for_date(target_date: date, limit: int | None = None) -> List[Dict[str, Any]]:
    """People born on a calendar day, most widely known first (optionally only the top `limit`)."""
    db = _load_births_db()
    if not db:
        return []

    day_entries = db.get(str(target_date.month), {}).get(str(target_date.day), [])
    people = [
        p
        for p in day_entries
        if isinstance(p, dict) and p.get("n") and isinstance(p.get("y"), int)
    ]
    people.sort(key=lambda p: (-(p.get("s") or 0), -p["y"]))
    return people if limit is None else people[:limit]


def _resolve_count(config: Dict[str, Any] | None) -> int:
    try:
        count = int((config or {}).get("count", DEFAULT_COUNT))
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return max(1, min(count, MAX_COUNT))


def _label(person: Dict[str, Any]) -> str:
    occupation = person.get("o")
    # The list is actors today; name the occupation only when it is something else.
    if occupation and occupation != "actor":
        return f"{person['n']} ({occupation})"
    return person["n"]


@register_module(
    type_id="birthdays",
    label="Celebrity Birthdays",
    description="Famous actors born on today's date",
    icon="cake",
    offline=True,
    category="content",
    config_schema={
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "title": "Number of Birthdays",
                "default": DEFAULT_COUNT,
                "minimum": 1,
                "maximum": MAX_COUNT,
            },
        },
    },
)
def format_birthdays_receipt(printer, config: Dict[str, Any] = None, module_name: str = None):
    """Prints the most widely known people born on today's date."""

    target_date = _resolve_reference_date(config)
    people = get_birthdays_for_date(target_date, _resolve_count(config))

    printer.print_header(module_name or "BIRTHDAYS", icon="cake")
    printer.print_caption(target_date.strftime("%B %d, %Y"))
    printer.print_line()

    if not people:
        printer.print_body("No famous birthdays")
        printer.print_body("found for today.")
        return

    # Oldest birth year first reads like a timeline.
    items = [
        {"year": person["y"], "text": _label(person)}
        for person in sorted(people, key=lambda p: (p["y"], p["n"]))
    ]
    width = getattr(printer, "PRINTER_WIDTH_DOTS", 384)
    regular_font = getattr(printer, "_get_font", lambda s: None)("regular")
    printer.print_image(draw_timeline_image(width, items, item_height=24, font=regular_font))
