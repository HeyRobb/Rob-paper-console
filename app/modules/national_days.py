"""Today's national days and fun ways to celebrate them, fetched live from nationaltoday.com.

Nothing from the site is stored in this repository. When the receipt prints, the
device downloads today's holiday list, then one page for each holiday it is going
to print, and keeps the parsed result in memory for the rest of the day so a
second print costs no requests at all. The optional "Upcoming Days" section also
reads the site's page for each of the next few dates (see load_upcoming_days).

Being a good guest matters here, so the fetching is deliberately modest:
  * only nationaltoday.com pages are requested, and never the paths its
    robots.txt disallows (/resources/, /wp-admin/);
  * requests are sequential, identify themselves, and are time-boxed so a slow
    connection cannot stall the printer for long;
  * at most 1 + MAX_COUNT pages (plus UPCOMING_LOOKAHEAD_DAYS date pages when
    "Upcoming Days" is on) are fetched per day, however often it prints.

The parsers key off headings ("All holidays today", "How To Celebrate ...")
instead of CSS classes, which change far more often. When the list heading
cannot be found the receipt says so rather than guessing: a wrong list printed
as today's would be worse than no list. Run scripts/check_national_days.py to see
exactly what the parsers make of the live pages.
"""

import logging
import re
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from app.module_registry import register_module
from app.modules.history import _resolve_reference_date

logger = logging.getLogger(__name__)

SITE_HOST = "nationaltoday.com"
TODAY_URL = f"https://{SITE_HOST}/what-is-today/"
SOURCE_CREDIT = "via nationaltoday.com"

USER_AGENT = "Paper Console/1 (personal thermal-printer project)"
REQUEST_TIMEOUT = (4, 8)  # (connect, read) seconds
TOTAL_BUDGET_SECONDS = 25  # stop fetching more pages once a print has used this long
REQUEST_PAUSE_SECONDS = 0.5  # courtesy gap between holiday-page requests
MAX_PAGE_BYTES = 2_000_000

# Paths the site's robots.txt asks crawlers to stay out of.
DISALLOWED_PREFIXES = ("/resources/", "/wp-admin/")

DEFAULT_COUNT = 3
MAX_COUNT = 5
DEFAULT_IDEAS = 2
MAX_IDEAS = 10

# "Upcoming Days": the next few dates' arts, food and fun holidays, name and category under a date heading.
MIN_UPCOMING = 3  # dates listed
DEFAULT_UPCOMING = 3
MAX_UPCOMING = 5
DEFAULT_UPCOMING_PER_DAY = 2  # holidays listed under each date
MAX_UPCOMING_PER_DAY = 5
UPCOMING_LOOKAHEAD_DAYS = 7  # date pages looked at, tomorrow onwards, to find the dates to list
UPCOMING_BUDGET_SECONDS = 20
UPCOMING_CATEGORIES = ("arts & entertainment", "food & beverage", "fun")
MONTH_SLUGS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

# Single-segment pages that are navigation, not holidays.
NON_HOLIDAY_SLUGS = {
    "today", "what-is-today", "holidays", "birthdays", "reminders", "calendar",
    "national-today-calendar", "national-day-calendar", "about-us", "contact",
    "contact-us", "privacy-policy", "terms", "terms-of-use", "services",
    "resources", "login", "log-in", "sign-up", "signup", "search", "videos",
    "gift-guides", "shopping-reviews", "months", "publishing-standards",
    "our-publishing-standards", "popular-holidays",
}

# Category labels the site shows beside each entry, with the "Skip ..." checkbox for each
# (same convention as the History module: a ticked box leaves that kind of day out, because
# the settings form shows an unset checkbox as unchecked). The order here is also the print
# order: things you can play, eat or drink come first, awareness days last.
CATEGORIES = [
    ("arts & entertainment", "exclude_arts_entertainment", "Skip arts & entertainment days"),
    ("food & beverage", "exclude_food_beverage", "Skip food & beverage days"),
    ("fun", "exclude_fun", "Skip fun days"),
    ("animal", "exclude_animal", "Skip animal days"),
    ("special interest", "exclude_special_interest", "Skip special interest days"),
    ("relationship", "exclude_relationship", "Skip relationship days"),
    ("cultural", "exclude_cultural", "Skip cultural days"),
    ("appreciation", "exclude_appreciation", "Skip appreciation days"),
    ("religious", "exclude_religious", "Skip religious days"),
    ("health", "exclude_health", "Skip health days"),
    ("cause", "exclude_cause", "Skip cause days"),
    ("federal", "exclude_federal", "Skip federal days"),
]
CATEGORY_LABELS = {label for label, _key, _title in CATEGORIES}
# The site sometimes words a category in the plural (its footer says "Animals", "Relationships").
CATEGORY_ALIASES = {"animals": "animal", "relationships": "relationship"}
CATEGORY_RANK = {label: position for position, (label, _key, _title) in enumerate(CATEGORIES)}
# Days the site doesn't label sort after appreciation and religious days, before health, cause and federal.
UNLABELED_RANK = CATEGORY_RANK["health"] - 0.5

_WS = re.compile(r"\s+")
_DATE_LABEL = re.compile(
    r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}$", re.IGNORECASE
)
_SLUG_PATH = re.compile(r"/[a-z0-9][a-z0-9-]*/?")
_LISTING_SLUG = re.compile(r"-(?:holidays|birthdays)$")
# "All holidays today" on the daily page, "All holidays on November 20" on a date page.
_HOLIDAYS_HEADING = re.compile(r"\bholidays today\b|\btoday'?s holidays\b|\ball holidays on\b", re.IGNORECASE)
_DATE_SLUG = re.compile(rf"(?:{'|'.join(MONTH_SLUGS)})-\d{{1,2}}")
# The section's h2 is worded per page: "How To Celebrate X", "How to Observe X",
# "Ways to Celebrate X" or "X Activities". "How Businesses Can Celebrate X" is
# deliberately not matched: it is advice for companies, not ideas for people.
_CELEBRATE_HEADING = re.compile(
    r"^(?:how to|ways to)\s+(?:celebrate|observe|honou?r|mark|enjoy|spend)\b|\bactivities$",
    re.IGNORECASE,
)
_HEADING = re.compile(r"h[1-6]")
_NOISE = {"advertisement", "sponsored", "ad"}


# ---- parsing ---------------------------------------------------------------


def _clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def _site_host_ok(host: str | None) -> bool:
    return (host or "").lower() in (SITE_HOST, f"www.{SITE_HOST}")


def _holiday_url(href: str | None) -> Optional[str]:
    """Normalise a link to a holiday page, or None when it is anything else."""
    if not href:
        return None
    parts = urlparse(urljoin(TODAY_URL, href.strip()))
    if parts.scheme not in ("http", "https") or not _site_host_ok(parts.hostname):
        return None
    if not _SLUG_PATH.fullmatch(parts.path):
        return None
    if any(parts.path.startswith(prefix) for prefix in DISALLOWED_PREFIXES):
        return None
    slug = parts.path.strip("/")
    if slug in NON_HOLIDAY_SLUGS or _LISTING_SLUG.search(slug) or _DATE_SLUG.fullmatch(slug):
        return None
    return f"https://{SITE_HOST}/{slug}/"


def _as_category(text: str | None) -> Optional[str]:
    """The known category label that text is (ignoring case and plural wording), else None."""
    label = _clean(text).lower()
    label = CATEGORY_ALIASES.get(label, label)
    return label if label in CATEGORY_LABELS else None


def _link_name(anchor: Tag) -> str:
    """The holiday name inside a link, ignoring any date or category labels."""
    parts = [_clean(s) for s in anchor.stripped_strings]
    parts = [p for p in parts if p and not _DATE_LABEL.match(p) and not _as_category(p)]
    return max(parts, key=len) if parts else ""


def _category_for(anchor: Tag) -> Optional[str]:
    """The category label printed beside one list entry, if it can be found."""
    for ancestor in [anchor, *list(anchor.parents)[:4]]:  # the card may itself be the link
        if not isinstance(ancestor, Tag):
            break
        urls = {_holiday_url(a.get("href")) for a in ancestor.find_all("a", href=True)}
        urls.discard(None)
        if len(urls) > 1:  # climbed out of this entry into the whole list
            break
        for text in ancestor.stripped_strings:
            label = _as_category(text)
            if label:
                return label
    return None


def _in_page_chrome(tag: Tag) -> bool:
    return tag.find_parent(["nav", "footer", "aside", "header"]) is not None


def _anchors_under_holiday_list_heading(soup: BeautifulSoup) -> List[Tag]:
    heading = next(
        (h for h in soup.find_all(["h1", "h2", "h3", "h4"]) if _HOLIDAYS_HEADING.search(_clean(h.get_text(" ")))),
        None,
    )
    if heading is None:
        return []

    anchors: List[Tag] = []
    for el in heading.next_elements:
        if not isinstance(el, Tag):
            continue
        if el.name == "footer" or (el is not heading and el.name in ("h1", "h2")):
            break  # the list ends at the next section or the footer
        if el.name == "a" and el.has_attr("href") and not _in_page_chrome(el):
            anchors.append(el)
    return anchors


def parse_today_page(html: str | None) -> List[Dict[str, Any]]:
    """Holidays from the daily page or a date page, in page order: [{"name", "url", "category"}].

    Only links beneath the "All holidays today" (or "All holidays on November 20")
    heading count. Returns an empty list when that heading is missing instead of
    guessing from other links.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    holidays: List[Dict[str, Any]] = []
    seen = set()
    for anchor in _anchors_under_holiday_list_heading(soup):
        url = _holiday_url(anchor.get("href"))
        name = _link_name(anchor)
        if not url or not name or url in seen:  # thumbnails are links with no text
            continue
        seen.add(url)
        holidays.append({"name": name, "url": url, "category": _category_for(anchor)})
    return holidays


def _section_after(heading: Tag, level: int):
    """Elements after a heading, up to the next heading of the same or higher level."""
    for el in heading.next_elements:
        if isinstance(el, Tag) and (el.name == "footer" or (_HEADING.fullmatch(el.name) and int(el.name[1]) <= level)):
            return
        yield el


def _ideas_from_subheadings(section: List[Any]) -> List[Dict[str, str]]:
    ideas: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    title_tag: Optional[Tag] = None
    for el in section:
        if isinstance(el, Tag) and _HEADING.fullmatch(el.name):
            title_tag = el
            current = {"title": _clean(el.get_text(" ")), "parts": []}
            ideas.append(current)
            continue
        if current is None or not isinstance(el, NavigableString) or isinstance(el, Comment):
            continue
        if any(parent is title_tag for parent in el.parents) or el.find_parent(["script", "style"]):
            continue
        text = _clean(str(el))
        if text and text.lower() not in _NOISE:
            current["parts"].append(text)
    return [{"title": i["title"], "text": _clean(" ".join(i["parts"]))} for i in ideas]


def _ideas_from_list_items(section: List[Any]) -> List[Dict[str, str]]:
    ideas = []
    for el in section:
        if not (isinstance(el, Tag) and el.name == "li"):
            continue
        full = _clean(el.get_text(" "))
        lead = el.find(["strong", "b"])
        lead_text = _clean(lead.get_text(" ")) if lead else ""
        if lead_text:
            title = lead_text.rstrip(":.")
            text = _clean(full.replace(lead_text, "", 1)).lstrip(":.- ")
        else:
            match = re.match(r"(.{3,80}?)[.:!?](?:\s+(.*))?$", full)
            title, text = (match.group(1), match.group(2) or "") if match else (full[:80], "")
        ideas.append({"title": title, "text": text})
    return ideas


def parse_celebrate_ideas(html: str | None) -> List[Dict[str, str]]:
    """The "How To Celebrate ..." ideas from a holiday page: [{"title", "text"}].

    "How Businesses Can Celebrate ..." and similar sections are deliberately not
    matched. Returns an empty list when the page has no such section.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    # h2 only: the page's FAQ questions are h3s and must not be mistaken for the section.
    heading = next(
        (h for h in soup.find_all("h2") if _CELEBRATE_HEADING.search(_clean(h.get_text(" ")))),
        None,
    )
    if heading is None:
        return []

    section = list(_section_after(heading, int(heading.name[1])))
    ideas = _ideas_from_subheadings(section) or _ideas_from_list_items(section)
    return [idea for idea in ideas if idea["title"]]


# ---- fetching --------------------------------------------------------------


def _date_url(day: date) -> str:
    """The site's page for one date, e.g. https://nationaltoday.com/november-20/."""
    return f"https://{SITE_HOST}/{MONTH_SLUGS[day.month - 1]}-{day.day}/"


def _is_date_url(url: str) -> bool:
    parts = urlparse(url)
    return (
        parts.scheme == "https"
        and parts.hostname == SITE_HOST
        and bool(_DATE_SLUG.fullmatch(parts.path.strip("/")))
        and not parts.query
        and parts.path == f"/{parts.path.strip('/')}/"
    )


def _fetch_html(url: str, deadline: float) -> Optional[str]:
    """Download one nationaltoday.com page, or None when it should not or cannot be fetched."""
    if url != TODAY_URL and not _is_date_url(url) and _holiday_url(url) is None:
        return None
    if time.monotonic() >= deadline:
        return None
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:  # network, DNS, timeout, HTTP error: the receipt just says so
        logger.info("national_days: could not fetch %s: %s", url, exc)
        return None

    final_host = urlparse(getattr(response, "url", "") or url).hostname
    if not _site_host_ok(final_host) or len(response.content) > MAX_PAGE_BYTES:
        return None  # redirected off-site, or not a normal page
    return response.text


# ---- per-day cache ---------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"date": None, "holidays": None, "ideas": {}}
_upcoming_cache: Dict[date, List[Dict[str, Any]]] = {}  # parsed date pages, kept until that date has passed


def _reset_cache() -> None:
    with _cache_lock:
        _cache.update(date=None, holidays=None, ideas={})
        _upcoming_cache.clear()


def _fun_rank(holiday: Dict[str, Any]) -> float:
    """Sort key for the default print order (see CATEGORIES); ties keep the site's order."""
    return CATEGORY_RANK.get(holiday.get("category"), UNLABELED_RANK)


def _skipped_categories(config: Dict[str, Any] | None) -> frozenset:
    """Category labels switched off by the "Skip ..." checkboxes."""
    cfg = config or {}
    return frozenset(label for label, key, _title in CATEGORIES if cfg.get(key))


def load_national_days(
    target_date: date,
    count: int,
    idea_limit: int,
    keep_page_order: bool = False,
    skip_categories: frozenset = frozenset(),
) -> Optional[List[Dict[str, Any]]]:
    """Today's holidays with their celebration ideas, or None when the list cannot be loaded.

    An empty list means the list loaded but every holiday was skipped by category.
    Days with no recognised category label are never skipped, since they can't be
    classified. A failed load is not cached, so the next print tries again; holiday
    pages that fail or have no ideas still come back, with an empty idea list.
    Skipping happens after the per-day cache, so changing it never costs a request.
    """
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    with _cache_lock:
        if _cache["date"] != target_date:
            _cache.update(date=target_date, holidays=None, ideas={})
        holidays = _cache["holidays"]

    if holidays is None:
        html = _fetch_html(TODAY_URL, deadline)
        holidays = parse_today_page(html) if html else []
        if not holidays:
            return None
        with _cache_lock:
            _cache["holidays"] = holidays

    wanted = [h for h in holidays if h["category"] not in skip_categories]
    chosen = wanted if keep_page_order else sorted(wanted, key=_fun_rank)  # stable sort
    results: List[Dict[str, Any]] = []
    fetched_any = False
    for holiday in chosen[:count]:
        with _cache_lock:
            ideas = _cache["ideas"].get(holiday["url"])
        if ideas is None:
            if fetched_any and REQUEST_PAUSE_SECONDS:
                time.sleep(REQUEST_PAUSE_SECONDS)
            html = _fetch_html(holiday["url"], deadline)
            fetched_any = True
            if html is not None:
                ideas = parse_celebrate_ideas(html)
                with _cache_lock:
                    _cache["ideas"][holiday["url"]] = ideas
        results.append(
            {
                "name": holiday["name"],
                "url": holiday["url"],
                "category": holiday["category"],
                "ideas": (ideas or [])[:idea_limit],
            }
        )
    return results


def load_upcoming_days(
    today: date,
    days: int,
    per_day: int,
    skip_categories: frozenset = frozenset(),
    lookahead: int = UPCOMING_LOOKAHEAD_DAYS,
) -> Optional[List[Dict[str, Any]]]:
    """The next dates with arts, food or fun holidays: [{"date", "holidays": [{"name", "url", "category"}]}].

    Looks at the site's page for tomorrow, then the day after, and so on (at most
    `lookahead` dates), until `days` dates that have a matching holiday are found, so
    a date with nothing fun is simply left out. Under each date go at most `per_day`
    holidays: arts before food before fun, then the site's order. Categories ticked in
    the "Skip ..." checkboxes are left out. Each date page is kept in memory until that
    date has passed, so repeat prints cost nothing.

    Returns None when no date page could be loaded or understood, and [] when pages
    loaded but nothing matched. A page that fails is not cached, and a failure after
    some pages loaded still returns what was found so far.
    """
    allowed = {c for c in UPCOMING_CATEGORIES if c not in skip_categories}
    if not allowed:
        return []

    deadline = time.monotonic() + UPCOMING_BUDGET_SECONDS
    with _cache_lock:
        for stale in [d for d in _upcoming_cache if d <= today]:
            del _upcoming_cache[stale]

    groups: List[Dict[str, Any]] = []
    seen_urls = set()
    loaded_any = False
    fetched_any = False
    for offset in range(1, lookahead + 1):
        if len(groups) >= days:
            break
        day = today + timedelta(days=offset)
        with _cache_lock:
            holidays = _upcoming_cache.get(day)
        if holidays is None:
            if time.monotonic() >= deadline:
                break
            if fetched_any and REQUEST_PAUSE_SECONDS:
                time.sleep(REQUEST_PAUSE_SECONDS)
            html = _fetch_html(_date_url(day), deadline)
            fetched_any = True
            holidays = parse_today_page(html) if html else []
            if not holidays:
                continue  # unreachable or not understood: not cached, try the next date
            with _cache_lock:
                _upcoming_cache[day] = holidays
        loaded_any = True
        matching = [h for h in holidays if h["category"] in allowed and h["url"] not in seen_urls]
        chosen = sorted(matching, key=_fun_rank)[:per_day]  # stable: ties keep the site's order
        if chosen:
            seen_urls.update(h["url"] for h in chosen)
            groups.append({"date": day, "holidays": chosen})
    return groups if loaded_any else None


# ---- receipt ---------------------------------------------------------------


def _resolve_int(
    config: Dict[str, Any] | None, key: str, default: int, maximum: int, minimum: int = 1
) -> int:
    try:
        value = int((config or {}).get(key, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _category_title(label: str | None) -> str:
    """The site's category label for printing, e.g. "food & beverage" -> "Food & Beverage"."""
    return label.title() if label else ""


def _day_line(target_date: date, category: str | None) -> str:
    """Date and category under each day's name, e.g. "Nov 19 - Arts & Entertainment"."""
    parts = [f"{target_date:%b} {target_date.day}", _category_title(category)]
    return " - ".join(part for part in parts if part)


def _upcoming_heading(day: date) -> str:
    """Bold line above each upcoming date's holidays, e.g. "Fri, Nov 20"."""
    return f"{day:%a}, {day:%b} {day.day}"


def _print_upcoming(printer, config: Dict[str, Any], target_date: date) -> bool:
    """Prints the "Upcoming Days" section; True when it printed anything from the site."""
    days = _resolve_int(config, "upcoming_count", DEFAULT_UPCOMING, MAX_UPCOMING, MIN_UPCOMING)
    per_day = _resolve_int(config, "upcoming_per_day", DEFAULT_UPCOMING_PER_DAY, MAX_UPCOMING_PER_DAY)
    upcoming = load_upcoming_days(target_date, days, per_day, _skipped_categories(config))

    printer.feed(1)  # a gap, a dashed line and a bold title set the section apart from today's list
    printer.print_line()
    printer.print_bold("UPCOMING DAYS")
    if upcoming is None:
        printer.print_body("Couldn't load upcoming days.")
        return False
    if not upcoming:
        printer.print_body("Nothing matching in the")
        printer.print_body(f"next {UPCOMING_LOOKAHEAD_DAYS} days.")
        return False
    for group in upcoming:
        printer.print_line()  # a rule above every date, so the dates stand out at a glance
        printer.print_bold(_upcoming_heading(group["date"]))
        for holiday in group["holidays"]:
            printer.print_body(holiday["name"])  # name only: the section is already arts, food and fun
    return True


@register_module(
    type_id="national_days",
    label="National Days",
    description="Today's fun national days and ways to celebrate them (live from nationaltoday.com)",
    icon="confetti",
    offline=False,
    category="content",
    config_schema={
        "type": "object",
        "description": (
            "The top of the receipt lists today's national days with ways to celebrate them "
            "(the \"Today\" settings). Turn on the Upcoming Days section for a short list of the "
            "next few dates (the \"Upcoming\" settings). Arts, food and fun days print first, then "
            "animal, special interest, relationship, cultural, appreciation and religious days, then "
            "health, cause and federal days, unless you keep the site's order. Tick a \"Skip\" box to "
            "leave that kind of day out of both lists; days the site doesn't label with a category "
            "are always kept in today's list."
        ),
        "properties": {
            "count": {
                "type": "integer",
                "title": "Today: Days to Print",
                "description": "How many of today's national days print at the top, each with its ideas.",
                "default": DEFAULT_COUNT,
                "minimum": 1,
                "maximum": MAX_COUNT,
            },
            "ideas": {
                "type": "integer",
                "title": "Today: Ideas per Day",
                "description": "Most days list about 3 ideas, so a higher number rarely prints more.",
                "default": DEFAULT_IDEAS,
                "minimum": 1,
                "maximum": MAX_IDEAS,
            },
            "keep_page_order": {
                "type": "boolean",
                "title": "Today: Keep the site's order (otherwise arts, food and fun days print first)",
                "default": False,
            },
            "show_upcoming": {
                "type": "boolean",
                "title": "Upcoming: Add an Upcoming Days section (arts, food and fun only)",
                "default": False,
            },
            "upcoming_count": {
                "type": "integer",
                "title": "Upcoming: Dates to List",
                "description": (
                    "How many future dates the Upcoming Days section lists. A date with no arts, "
                    f"food or fun day is left out; looks up to {UPCOMING_LOOKAHEAD_DAYS} days ahead."
                ),
                "default": DEFAULT_UPCOMING,
                "minimum": MIN_UPCOMING,
                "maximum": MAX_UPCOMING,
            },
            "upcoming_per_day": {
                "type": "integer",
                "title": "Upcoming: Days per Date",
                "description": "The most national days listed under each upcoming date (arts first, then food, then fun).",
                "default": DEFAULT_UPCOMING_PER_DAY,
                "minimum": 1,
                "maximum": MAX_UPCOMING_PER_DAY,
            },
            **{key: {"type": "boolean", "title": title, "default": False} for _label, key, title in CATEGORIES},
        },
    },
)
def format_national_days_receipt(printer, config: Dict[str, Any] = None, module_name: str = None):
    """Prints a few of today's national days, each with short ways to celebrate."""

    config = config or {}
    target_date = _resolve_reference_date(config)
    count = _resolve_int(config, "count", DEFAULT_COUNT, MAX_COUNT)
    idea_limit = _resolve_int(config, "ideas", DEFAULT_IDEAS, MAX_IDEAS)

    printer.print_header(module_name or "NATIONAL DAYS", icon="confetti")
    printer.print_caption(target_date.strftime("%B %d, %Y"))
    printer.print_line()

    days = load_national_days(
        target_date, count, idea_limit, bool(config.get("keep_page_order")), _skipped_categories(config)
    )
    if days is None:
        printer.print_body("Couldn't load today's")
        printer.print_body("national days. Check the")
        printer.print_body("internet connection.")
        return

    printed_site_content = False
    if not days:
        printer.print_body("No national days today")
        printer.print_body("match your settings.")
    else:
        printed_site_content = True
        for index, day in enumerate(days):
            if index:
                printer.feed(1)
            printer.print_bold(day["name"])
            printer.print_caption(_day_line(target_date, day["category"]))
            if not day["ideas"]:
                continue
            printer.print_caption("Ways to celebrate:")
            for idea in day["ideas"]:
                printer.print_body(f"- {idea['title']}")

    if config.get("show_upcoming"):
        printed_site_content = _print_upcoming(printer, config, target_date) or printed_site_content

    if not printed_site_content:
        return
    printer.print_line()
    printer.print_caption(SOURCE_CREDIT)
