#!/usr/bin/env python3
"""Check the National Days module against the live nationaltoday.com pages.

The module scrapes nationaltoday.com at print time, so the one thing unit tests
cannot prove is that the parsers still understand the real pages. Run this on a
machine with normal internet access (a laptop, or the device itself) after
installing, and whenever the receipt starts saying it "couldn't load" the days:

    ./.venv/bin/python scripts/check_national_days.py
    ./.venv/bin/python scripts/check_national_days.py --count 5 --ideas 4
    ./.venv/bin/python scripts/check_national_days.py --save-html /tmp/nt

It runs the module's real fetch and parse path, lists every holiday it found for
today, and shows the receipt as plain text. --save-html also keeps the raw pages
so a parsing problem can be inspected without fetching again. At most 1 + count
pages are requested, the same as one print.

Exit status: 0 = receipt would print, 1 = a page could not be fetched,
2 = fetched but the holiday list could not be found in it.
"""

import argparse
import platform
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing the app on a Raspberry Pi opens the real printer serial port and GPIO pins (about
# 20 s on a Pi Zero, and it competes with the running service for the port). This script only
# talks to nationaltoday.com, so present as a non-Pi and let the app pick its mock drivers.
platform.system = lambda: "Darwin"

from bs4 import BeautifulSoup  # noqa: E402

from app.modules import national_days as nd  # noqa: E402

_clean = nd._clean


class _TextPrinter:
    """Prints receipt calls as plain text so the layout can be eyeballed."""

    def print_header(self, text, icon=None):
        print(f"\n=== {text.upper()} ===" + (f"  [icon: {icon}]" if icon else ""))

    def print_caption(self, text):
        print(f"  ({text})")

    def print_line(self):
        print("  " + "-" * 30)

    def print_body(self, text):
        print(f"  {text}")

    def print_bold(self, text):
        print(f"  ** {text} **")

    def feed(self, lines=1):
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--count", type=int, default=nd.DEFAULT_COUNT, help="days to print (default %(default)s)")
    parser.add_argument("--ideas", type=int, default=nd.DEFAULT_IDEAS, help="ideas per day (default %(default)s)")
    parser.add_argument("--keep-page-order", action="store_true", help="print in the site's order instead of by category")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=sorted(nd.CATEGORY_LABELS),
        metavar="CATEGORY",
        help="leave a category out, like ticking its Skip checkbox; repeatable, e.g. --skip cause "
        "--skip 'food & beverage'. One of: " + ", ".join(sorted(nd.CATEGORY_LABELS)),
    )
    parser.add_argument("--save-html", metavar="DIR", help="also save each fetched page here")
    args = parser.parse_args()

    save_dir = Path(args.save_html) if args.save_html else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    real_fetch = nd._fetch_html
    list_fetch_failed = False
    pages = {}

    def reporting_fetch(url, deadline):
        nonlocal list_fetch_failed
        started = time.monotonic()
        html = real_fetch(url, deadline)
        elapsed = time.monotonic() - started
        if url == nd.TODAY_URL and html is None:
            list_fetch_failed = True
        if html is not None:
            pages[url] = html
        outcome = f"ok, {len(html):,} characters" if html is not None else "FAILED"
        print(f"fetch {url} -> {outcome} ({elapsed:.1f} s)")
        if html is not None and save_dir:
            name = urlparse(url).path.strip("/") or "index"
            (save_dir / f"{name}.html").write_text(html, encoding="utf-8")
        return html

    nd._fetch_html = reporting_fetch

    day = nd._resolve_reference_date({})
    load_started = time.monotonic()
    days = nd.load_national_days(day, args.count, args.ideas, args.keep_page_order, frozenset(args.skip))
    load_seconds = time.monotonic() - load_started

    holidays = nd._cache["holidays"]
    if holidays is None:
        if list_fetch_failed:
            print("\nCould not download the daily list (see above). Check the internet connection.")
            return 1
        print("\nThe daily page downloaded, but no holidays could be found in it: it probably no longer has an")
        print("'All holidays today' heading with holiday links under it. Re-run with --save-html DIR and look")
        print("at what was saved, then adjust parse_today_page in app/modules/national_days.py.")
        return 2

    print(f"\nFound {len(holidays)} holidays for {day:%B %d, %Y} (page order):")
    for holiday in holidays:
        print(f"  [{holiday['category'] or '?':<20}] {holiday['name']}")
    missing_categories = sum(1 for h in holidays if not h["category"])
    if missing_categories:
        print(f"  note: {missing_categories} had no recognised category label, so they sort between fun and serious days.")

    empty = [d for d in days or [] if not d["ideas"]]
    if empty:
        print("\nNo ways-to-celebrate ideas were found for:")
        for day_without_ideas in empty:
            html = pages.get(day_without_ideas["url"])
            if html is None:
                print(f"  - {day_without_ideas['name']}: the page could not be fetched")
                continue
            h2s = [_clean(h.get_text(" ")) for h in BeautifulSoup(html, "html.parser").find_all("h2")]
            print(f"  - {day_without_ideas['name']}: its h2 headings are: " + " | ".join(h[:60] for h in h2s[:20]))
        print("If one of those headings is the ideas section, add its wording to _CELEBRATE_HEADING in")
        print("app/modules/national_days.py. (--save-html DIR keeps the pages so you can look at them.)")

    print("\nReceipt as it would print:")
    config = {
        "count": args.count,
        "ideas": args.ideas,
        "include_details": True,
        "keep_page_order": args.keep_page_order,
        **{key: True for label, key, _title in nd.CATEGORIES if label in args.skip},
    }
    nd.format_national_days_receipt(_TextPrinter(), config, None)
    print(f"\nTime this adds to a print: {load_seconds:.1f} s the first time each day, then none (cached).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
