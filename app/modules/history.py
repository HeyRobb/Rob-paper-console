import json
from typing import Dict, Any, Iterable, List
from pathlib import Path
from datetime import date, datetime
from app.utils import wrap_text, wrap_text_pixels
from app.config import current_date
from app.history_rules import GRIM_PATTERN
from PIL import Image, ImageDraw
from app.module_registry import register_module

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Classic "YEAR: text" history (every kind of event, unfiltered).
LOCAL_DB_PATH = DATA_DIR / "history.json"

# Curated/tagged pop-culture, tech and gaming milestones, built offline by
# scripts/build_history_data.py. Shape: {"events": {month: {day: [entry]}}}
# where entry = {"y": year, "t": text, "c": category, "s": score}.
THEMED_DB_PATH = DATA_DIR / "history_themed.json"

THEME_POP = "Pop culture & tech"
THEME_CLASSIC = "Classic history"
THEMES = [THEME_POP, THEME_CLASSIC]
DEFAULT_THEME = THEME_POP

# Genres inside the Pop culture & tech theme: (config key, category stored in
# history_themed.json, checkbox title shown in settings).
#
# These are "skip" toggles on purpose: the settings form shows an unset
# checkbox as unchecked (it ignores schema defaults), so an unchecked box has to
# mean "include this genre" or existing modules would look different from how
# they behave.
GENRES = [
    ("exclude_film_tv", "film_tv", "Skip film & TV"),
    ("exclude_music", "music", "Skip music"),
    ("exclude_games", "games", "Skip video games"),
    ("exclude_tech", "tech", "Skip tech & internet"),
    ("exclude_culture", "culture", "Skip comics, toys & theme parks"),
]
# Entries in history_themed.json whose category is not listed here (for example
# "birthday" entries from an older build) are never printed; famous birthdays
# have their own module now (see birthdays.py).
ALL_GENRE_CATEGORIES = frozenset(category for _, category, _ in GENRES)

# Used only when a themed day has no entries: skip classic entries that are
# about wars, disasters, deaths, treaties and similar.
_GRIM_PATTERN = GRIM_PATTERN

_themed_cache: Dict[str, Any] = {"key": None, "data": None}


def get_events_for_date(target_date: date) -> List[str]:
    """Reads events for a specific date from the local database."""
    month = str(target_date.month)
    day = str(target_date.day)

    try:
        with open(LOCAL_DB_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)

            # Navigate structure: Month -> Day -> List of Strings
            month_data = history.get(month, {})
            events = month_data.get(day, [])

            return events
    except FileNotFoundError:
        return ["Offline history database is missing."]
    except Exception:
        return ["Error reading history database."]


def get_events_for_today() -> List[str]:
    """Reads events for the current date from the local database."""
    return get_events_for_date(current_date())


def _load_themed_db() -> Dict[str, Any] | None:
    """Load the themed database (month -> day -> entries), cached by mtime.

    Returns None when the file is missing or unreadable so callers can fall
    back to the classic database instead of printing an error receipt.
    """
    path = THEMED_DB_PATH
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None

    if _themed_cache["key"] == key:
        return _themed_cache["data"]

    data = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        events = raw.get("events") if isinstance(raw, dict) else None
        if isinstance(events, dict):
            data = events
    except (OSError, ValueError):
        data = None

    _themed_cache["key"] = key
    _themed_cache["data"] = data
    return data


def get_themed_events_for_date(
    target_date: date,
    theme: str = DEFAULT_THEME,
    categories: Iterable[str] | None = None,
) -> List[str]:
    """Themed events for a date as "YEAR: text" strings, best-scored first.

    `categories` limits which genres are used (default: all of them).
    """
    if theme != THEME_POP:
        return []
    categories = ALL_GENRE_CATEGORIES if categories is None else frozenset(categories)
    if not categories:
        return []

    db = _load_themed_db()
    if not db:
        return []

    day_entries = db.get(str(target_date.month), {}).get(str(target_date.day), [])
    picked = [
        e
        for e in day_entries
        if isinstance(e, dict) and e.get("c") in categories and e.get("t")
    ]
    picked.sort(key=lambda e: (-(e.get("s") or 0), -(e.get("y") or 0)))

    return [f"{e['y']}: {e['t']}" if e.get("y") else e["t"] for e in picked]


def get_fallback_events_for_date(target_date: date) -> List[str]:
    """Classic events for a date with wars/disasters/politics filtered out.

    If every classic entry for the day is filtered out, returns them all so
    the receipt is never empty just because of the filter.
    """
    classic = get_events_for_date(target_date)
    calm = [e for e in classic if not _GRIM_PATTERN.search(e)]
    return calm or classic


def get_events_for_theme(
    target_date: date,
    theme: str = DEFAULT_THEME,
    categories: Iterable[str] | None = None,
) -> List[str]:
    """Events for a date under the chosen theme, falling back to calmer classic history."""
    if theme == THEME_CLASSIC:
        return get_events_for_date(target_date)

    themed = get_themed_events_for_date(target_date, theme, categories)
    if themed:
        return themed
    return get_fallback_events_for_date(target_date)


def _resolve_theme(config: Dict[str, Any] | None) -> str:
    theme = (config or {}).get("theme")
    return theme if theme in THEMES else DEFAULT_THEME


def _resolve_categories(config: Dict[str, Any] | None) -> frozenset:
    """Genres still included after applying the "Skip ..." toggles."""
    cfg = config or {}
    return frozenset(category for key, category, _ in GENRES if not cfg.get(key))


def _resolve_reference_date(config: Dict[str, Any] | None) -> date:
    if not config:
        return current_date()

    raw_value = config.get("reference_date")
    if isinstance(raw_value, datetime):
        return raw_value.date()
    if isinstance(raw_value, date):
        return raw_value
    if isinstance(raw_value, str):
        try:
            return datetime.fromisoformat(raw_value).date()
        except ValueError:
            try:
                return date.fromisoformat(raw_value)
            except ValueError:
                pass
    return current_date()


@register_module(
    type_id="history",
    label="On This Day",
    description="Random pop-culture, tech and gaming milestones (or classic history) from today's date",
    icon="hourglass",
    offline=True,
    category="content",
    config_schema={
        "type": "object",
        "description": (
            "The skip toggles apply to the Pop culture & tech theme. Days with "
            "nothing left for your chosen genres print calmer classic history."
        ),
        "properties": {
            "theme": {
                "type": "string",
                "title": "Theme",
                "enum": THEMES,
                "default": DEFAULT_THEME,
            },
            "count": {"type": "integer", "title": "Number of Events", "default": 1},
            **{
                key: {"type": "boolean", "title": title, "default": False}
                for key, _category, title in GENRES
            },
        }
    }
)
def format_history_receipt(
    printer, config: Dict[str, Any] = None, module_name: str = None
):
    """Prints 'On This Day' historical events."""

    target_date = _resolve_reference_date(config)
    events = get_events_for_theme(
        target_date, _resolve_theme(config), _resolve_categories(config)
    )

    # Filter/Select events
    count = 1
    if config and "count" in config:
        try:
            count = int(config["count"])
        except:
            pass

    import random
    if len(events) > count:
        if config and config.get("reference_date"):
            selected_events = events[:count]
        else:
            selected_events = random.sample(events, count)
    else:
        selected_events = events

    # Header
    printer.print_header(module_name or "ON THIS DAY", icon="hourglass")
    printer.print_caption(target_date.strftime("%B %d, %Y"))
    printer.print_line()

    if not selected_events:
        printer.print_body("No historical records")
        printer.print_body("found for today.")
    else:
        # Prepare timeline items
        timeline_items = []
        for event in selected_events:
            year = 0
            description = event
            
            # Try to parse year from various formats:
            # "1920: Description" or "1920 - Description" or "1920 Description"
            if " - " in event:
                # Format: "YEAR - Description"
                year_str, description = event.split(" - ", 1)
                try:
                    year = int(year_str.strip())
                except:
                    year = 0
            elif ": " in event:
                # Format: "YEAR: Description" (most common in history.json)
                parts = event.split(": ", 1)
                if len(parts) == 2:
                    year_str = parts[0].strip()
                    description = parts[1].strip()
                    try:
                        year = int(year_str)
                    except:
                        year = 0
            else:
                # Try to extract year from start of string if it starts with digits
                parts = event.split(None, 1)  # Split on first whitespace
                if parts and parts[0].isdigit():
                    try:
                        year = int(parts[0])
                        description = parts[1] if len(parts) > 1 else event
                    except:
                        description = event
            
            timeline_items.append({"year": year, "text": description})
        
        # Print timeline graphic
        # Print timeline graphic
        width = getattr(printer, "PRINTER_WIDTH_DOTS", 384)
        regular_font = getattr(printer, "_get_font", lambda s: None)("regular")
        
        img = draw_timeline_image(
            width, 
            timeline_items, 
            item_height=24, 
            font=regular_font
        )
        printer.print_image(img)


def draw_timeline_image(
    width: int, 
    items: list, 
    item_height: int, 
    font
) -> Image.Image:
    """Draw a timeline graphic to an image."""
    
    # Calculate total height first (to create image)
    total_height = 0
    # Line height default
    line_height = getattr(font, "size", 24) if font else 24
    
    # We need to calculate height for each item to know total image height
    item_heights = []
    
    for item in items:
        # Calculate year width
        year = str(item.get("year", ""))
        year_width = 0
        if font and year and year != "0":
            try:
                bbox = font.getbbox(year)
                year_width = bbox[2] - bbox[0] if bbox else len(year) * 8
            except:
                year_width = len(year) * 8
        
        # Position vertical line after year with padding
        line_x = year_width + 12
        text_x = line_x + 10
        
        # Calculate available width for text
        max_text_width = width - text_x - 10
        
        # Calculate text height
        text = item.get("text", "")
        if font and text:
            wrapped_lines = wrap_text_pixels(text, font, max_text_width)
            actual_height = max(item_height, (len(wrapped_lines) * line_height) + 8)
        else:
            actual_height = item_height
            
        item_heights.append(actual_height)
        total_height += actual_height
        
    # Create image
    img = Image.new("1", (width, total_height), 1)  # White background
    draw = ImageDraw.Draw(img)
    
    current_y = 0
    x = 0
    
    for i, item in enumerate(items):
        item_y = current_y
        actual_height = item_heights[i]
        
        # Draw year label
        year = str(item.get("year", ""))
        year_width = 0
        if font and year and year != "0":
            try:
                bbox = font.getbbox(year)
                year_width = bbox[2] - bbox[0] if bbox else len(year) * 8
            except:
                year_width = len(year) * 8
            draw.text((x, item_y - 4), year, font=font, fill=0)
            
        # Draw vertical line
        line_x = x + year_width + 12
        text_x = line_x + 10
        max_text_width = width - text_x - 10
        
        # Draw text
        text = item.get("text", "")
        if font and text:
            wrapped_lines = wrap_text_pixels(text, font, max_text_width)
            text_y = item_y - 4
            for line in wrapped_lines:
                if line.strip():
                    draw.text((text_x, text_y), line, font=font, fill=0)
                    text_y += line_height
        elif text:
            draw.text((text_x, item_y - 4), text, fill=0)
            
        # Draw vertical timeline line to next item (if not last item)
        if i < len(items) - 1:
            line_end_y = item_y + actual_height
            draw.line([(line_x, item_y), (line_x, line_end_y)], fill=0, width=2)
            
        current_y += actual_height
        
    return img
