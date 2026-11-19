"""Tests for the National Days module (live fetch from nationaltoday.com).

No test touches the network: requests.get is replaced with a fake site built
from small HTML fixtures that follow the structure of the real pages (an
"All holidays today" list on /what-is-today/, a "How To Celebrate ..." section
on each holiday page).
"""

from datetime import date
from urllib.parse import urlparse

import pytest
import requests

from app import module_registry
from app.modules import national_days as nd

TODAY = date(2026, 11, 19)
TOMORROW = date(2026, 11, 20)


# ---- fixtures ---------------------------------------------------------------


def _entry(slug, name, category):
    return (
        f'<li><a href="/{slug}/"><img src="{slug}.jpg"></a>'
        f"<span>Nov19</span><span>{category}</span>"
        f'<a href="/{slug}/">{name}</a></li>'
    )


TODAY_HTML = f"""
<html><body>
<header><nav>
  <a href="/today/">Today</a><a href="/november-holidays/">Holidays</a>
  <a href="/november-19-birthdays/">Birthdays</a><a href="/nav-only-day/">Nav Only Day</a>
</nav></header>
<main>
  <h1>What Is Today?</h1>
  <p>Intro mentioning <a href="/intro-only-day/">Intro Only Day</a>.</p>
  <h2>All holidays today</h2>
  <ul>
    {_entry("great-american-smokeout", "Great American Smokeout", "Health")}
    {_entry("international-mens-day", "International Men's Day", "Cause")}
    {_entry("beaujolais-nouveau-day", "Beaujolais Nouveau Day", "Food &amp; Beverage")}
    {_entry("have-a-bad-day-day", "National Have A Bad Day Day", "Fun")}
    {_entry("national-camp-day", "National Camp Day", "Special Interest")}
  </ul>
  <aside><a href="/sidebar-only-day/">Sidebar Only Day</a></aside>
  <h2>About November 19</h2>
  <p>Also <a href="/discovery-of-puerto-rico-day/">Discovery of Puerto Rico Day</a>.</p>
</main>
<footer><a href="/popular-footer-day/">Popular Footer Day</a></footer>
</body></html>
"""

PAGE_ORDER = [
    "Great American Smokeout",
    "International Men's Day",
    "Beaujolais Nouveau Day",
    "National Have A Bad Day Day",
    "National Camp Day",
]
FUN_FIRST = [
    "Beaujolais Nouveau Day",
    "National Have A Bad Day Day",
    "National Camp Day",
    "Great American Smokeout",
    "International Men's Day",
]


def _holiday_page(name, ideas):
    items = "".join(f"<li><h3>{title}</h3><p>{text}</p></li>" for title, text in ideas)
    return f"""
    <html><body><main>
      <h1>{name} - November 19, 2026</h1><p>A short intro about {name}.</p>
      <h2>How Businesses Can Celebrate {name}</h2><p>Run a promotion.</p>
      <h2>How To Celebrate {name}</h2><ol>{items}</ol>
      <h2>5 Facts About {name}</h2><h3>Not an idea</h3><p>Just a fact.</p>
    </main></body></html>
    """


def _ideas_for(name):
    return [
        (f"{name} idea one", f"Do the first fun thing for {name}, with friends and snacks."),
        (f"{name} idea two", f"Do the second fun thing for {name}."),
        (f"{name} idea three", "Do the third thing."),
    ]


SLUGS = {
    "great-american-smokeout": "Great American Smokeout",
    "international-mens-day": "International Men's Day",
    "beaujolais-nouveau-day": "Beaujolais Nouveau Day",
    "have-a-bad-day-day": "National Have A Bad Day Day",
    "national-camp-day": "National Camp Day",
}


class FakeResponse:
    def __init__(self, text, url, status=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSite:
    """Stands in for requests.get and records every request."""

    def __init__(self):
        self.pages = {nd.TODAY_URL: TODAY_HTML}
        for slug, name in SLUGS.items():
            self.pages[f"https://nationaltoday.com/{slug}/"] = _holiday_page(name, _ideas_for(name))
        self.calls = []
        self.redirects = {}

    @property
    def urls(self):
        return [call["url"] for call in self.calls]

    def __call__(self, url, headers=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        page = self.pages.get(url)
        if page is None:
            raise requests.ConnectionError("no route")
        if isinstance(page, Exception):
            raise page
        if isinstance(page, int):
            return FakeResponse("", url, status=page)
        return FakeResponse(page, self.redirects.get(url, url))


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    nd._reset_cache()
    monkeypatch.setattr(nd, "REQUEST_PAUSE_SECONDS", 0)
    yield
    nd._reset_cache()


@pytest.fixture
def site(monkeypatch):
    fake = FakeSite()
    monkeypatch.setattr(nd.requests, "get", fake)
    return fake


class _CapturePrinter:
    PRINTER_WIDTH_DOTS = 384

    def __init__(self):
        self.calls = []

    def print_header(self, text, icon=None):
        self.calls.append(("header", text, icon))

    def print_caption(self, text):
        self.calls.append(("caption", text))

    def print_line(self):
        self.calls.append(("line",))

    def print_body(self, text):
        self.calls.append(("body", text))

    def print_bold(self, text):
        self.calls.append(("bold", text))

    def feed(self, lines=1):
        self.calls.append(("feed", lines))


def _print(config=None, name=None):
    printer = _CapturePrinter()
    nd.format_national_days_receipt(printer, {"reference_date": "2026-11-19", **(config or {})}, name)
    return printer.calls


# ---- link rules -------------------------------------------------------------


@pytest.mark.parametrize(
    "href, expected",
    [
        ("/apple-cider-day/", "https://nationaltoday.com/apple-cider-day/"),
        ("https://nationaltoday.com/apple-cider-day", "https://nationaltoday.com/apple-cider-day/"),
        ("http://www.nationaltoday.com/apple-cider-day/", "https://nationaltoday.com/apple-cider-day/"),
        ("https://evil.example/apple-cider-day/", None),
        ("https://nationaltoday.com.evil.example/apple-cider-day/", None),
        ("/november-holidays/", None),
        ("/november-19-birthdays/", None),
        ("/what-is-today/", None),
        ("/today/", None),
        ("/resources/some-guide/", None),
        ("/resources/", None),
        ("/wp-admin/", None),  # robots.txt disallows it even though it looks like a one-segment slug
        ("/wp-admin/admin-ajax.php", None),
        ("/category/fun/page/2/", None),
        ("/apple-cider-day/?utm=x", "https://nationaltoday.com/apple-cider-day/"),
        ("javascript:alert(1)", None),
        ("", None),
        (None, None),
    ],
)
def test_only_single_holiday_pages_on_the_site_are_accepted(href, expected):
    assert nd._holiday_url(href) == expected


# ---- today's list -------------------------------------------------------------


def test_today_page_returns_only_the_list_under_the_heading_in_page_order():
    holidays = nd.parse_today_page(TODAY_HTML)

    assert [h["name"] for h in holidays] == PAGE_ORDER
    assert holidays[0] == {
        "name": "Great American Smokeout",
        "url": "https://nationaltoday.com/great-american-smokeout/",
        "category": "health",
    }
    assert [h["category"] for h in holidays] == ["health", "cause", "food & beverage", "fun", "special interest"]


def test_links_in_nav_intro_other_sections_and_footer_are_ignored():
    urls = {h["url"] for h in nd.parse_today_page(TODAY_HTML)}

    for slug in (
        "nav-only-day", "intro-only-day", "sidebar-only-day", "discovery-of-puerto-rico-day", "popular-footer-day",
    ):
        assert f"https://nationaltoday.com/{slug}/" not in urls


def test_footer_links_are_ignored_when_the_list_is_the_last_section():
    html = (
        "<main><h2>All holidays today</h2>"
        '<ul><li><a href="/pizza-day/">National Pizza Day</a></li></ul></main>'
        '<footer><h3>Popular Holidays</h3><a href="/popular-footer-day/">Popular Footer Day</a></footer>'
    )

    assert [h["name"] for h in nd.parse_today_page(html)] == ["National Pizza Day"]


def test_page_without_the_list_heading_yields_nothing_rather_than_guessing():
    html = '<main><h2>Something else</h2><a href="/some-day/">Some Day</a></main>'

    assert nd.parse_today_page(html) == []
    assert nd.parse_today_page("") == []
    assert nd.parse_today_page(None) == []


def test_a_card_that_is_one_big_link_still_gives_name_and_category():
    html = (
        "<h2>All holidays today</h2>"
        '<a href="/pizza-day/"><img src="p.jpg"><span>Nov 19</span><span>Fun</span><span>National Pizza Day</span></a>'
    )

    assert nd.parse_today_page(html) == [
        {"name": "National Pizza Day", "url": "https://nationaltoday.com/pizza-day/", "category": "fun"}
    ]


def test_duplicate_links_to_the_same_holiday_appear_once():
    html = (
        "<h2>All holidays today</h2>"
        '<a href="/pizza-day/">National Pizza Day</a><a href="/pizza-day/">National Pizza Day</a>'
    )

    assert len(nd.parse_today_page(html)) == 1


# ---- celebration ideas ----------------------------------------------------------


def test_ideas_come_from_the_how_to_celebrate_section_only():
    ideas = nd.parse_celebrate_ideas(_holiday_page("Apple Cider Day", _ideas_for("Cider")))

    assert [i["title"] for i in ideas] == ["Cider idea one", "Cider idea two", "Cider idea three"]
    assert ideas[0]["text"] == "Do the first fun thing for Cider, with friends and snacks."
    # Not the "How Businesses Can Celebrate" section, and not the facts section after it.
    assert all("Not an idea" != i["title"] for i in ideas)


def test_ideas_are_also_read_from_plain_list_items():
    html = (
        "<h2>Ways to celebrate</h2><ol>"
        "<li><strong>Throw a party:</strong> Invite friends and neighbours.</li>"
        "<li>Bake something. Share it around.</li></ol><h2>Next section</h2><ol><li>Not me.</li></ol>"
    )

    assert nd.parse_celebrate_ideas(html) == [
        {"title": "Throw a party", "text": "Invite friends and neighbours."},
        {"title": "Bake something", "text": "Share it around."},
    ]


def test_page_without_a_celebrate_section_has_no_ideas():
    assert nd.parse_celebrate_ideas("<h2>How Businesses Can Celebrate X</h2><p>Promo.</p>") == []
    assert nd.parse_celebrate_ideas("") == []
    assert nd.parse_celebrate_ideas(None) == []


def test_advertisement_labels_between_ideas_are_not_part_of_the_text():
    html = "<h2>How To Celebrate Z Day</h2><h3>Do a thing</h3><p>Really do it.</p><div>Advertisement</div>"

    assert nd.parse_celebrate_ideas(html) == [{"title": "Do a thing", "text": "Really do it."}]


# ---- fetching and caching ---------------------------------------------------------


def test_load_fetches_the_list_then_only_the_holidays_it_will_print_fun_first(site):
    days = nd.load_national_days(TODAY, count=2, idea_limit=2)

    assert [d["name"] for d in days] == FUN_FIRST[:2]
    assert site.urls == [
        nd.TODAY_URL,
        "https://nationaltoday.com/beaujolais-nouveau-day/",
        "https://nationaltoday.com/have-a-bad-day-day/",
    ]
    assert [i["title"] for i in days[0]["ideas"]] == [
        "Beaujolais Nouveau Day idea one",
        "Beaujolais Nouveau Day idea two",
    ]


def test_keep_page_order_skips_the_fun_first_sorting(site):
    days = nd.load_national_days(TODAY, count=3, idea_limit=1, keep_page_order=True)

    assert [d["name"] for d in days] == PAGE_ORDER[:3]


def test_requests_identify_themselves_stay_on_site_and_avoid_disallowed_paths(site):
    nd.load_national_days(TODAY, count=5, idea_limit=1)

    assert len(site.calls) == 6
    for call in site.calls:
        parts = urlparse(call["url"])
        assert parts.scheme == "https" and parts.hostname == "nationaltoday.com"
        assert not parts.path.startswith(nd.DISALLOWED_PREFIXES)
        assert call["headers"]["User-Agent"] == nd.USER_AGENT
        assert call["timeout"] == nd.REQUEST_TIMEOUT


def test_second_load_on_the_same_day_makes_no_requests_and_a_new_day_refetches(site):
    first = nd.load_national_days(TODAY, count=2, idea_limit=2)
    requests_after_first = len(site.calls)

    again = nd.load_national_days(TODAY, count=2, idea_limit=2)
    assert again == first
    assert len(site.calls) == requests_after_first

    nd.load_national_days(TOMORROW, count=2, idea_limit=2)
    assert len(site.calls) == requests_after_first * 2


def test_a_bigger_request_later_in_the_day_only_fetches_what_is_missing(site):
    nd.load_national_days(TODAY, count=1, idea_limit=1)
    assert len(site.calls) == 2

    nd.load_national_days(TODAY, count=2, idea_limit=1)
    assert site.urls[2:] == ["https://nationaltoday.com/have-a-bad-day-day/"]


@pytest.mark.parametrize(
    "failure",
    [requests.ConnectionError("down"), requests.Timeout("slow"), 503, 404],
)
def test_failure_to_load_the_list_returns_none_and_is_retried_next_time(site, failure):
    site.pages[nd.TODAY_URL] = failure
    assert nd.load_national_days(TODAY, count=2, idea_limit=2) is None
    assert len(site.calls) == 1

    site.pages[nd.TODAY_URL] = TODAY_HTML  # the connection comes back
    assert nd.load_national_days(TODAY, count=2, idea_limit=2) is not None
    assert site.urls[1] == nd.TODAY_URL  # it asked again instead of remembering the failure


def test_a_holiday_page_that_fails_still_lists_the_holiday_without_ideas(site):
    site.pages["https://nationaltoday.com/beaujolais-nouveau-day/"] = 500

    days = nd.load_national_days(TODAY, count=2, idea_limit=2)

    assert days[0]["name"] == "Beaujolais Nouveau Day" and days[0]["ideas"] == []
    assert days[1]["ideas"]


def test_a_failed_holiday_page_is_retried_on_the_next_print(site):
    url = "https://nationaltoday.com/beaujolais-nouveau-day/"
    site.pages[url] = 500
    nd.load_national_days(TODAY, count=1, idea_limit=2)

    site.pages[url] = _holiday_page("Beaujolais Nouveau Day", _ideas_for("Beaujolais Nouveau Day"))
    days = nd.load_national_days(TODAY, count=1, idea_limit=2)

    assert days[0]["ideas"]


def test_a_redirect_off_site_is_not_trusted(site):
    site.redirects[nd.TODAY_URL] = "https://ads.example.net/landing"

    assert nd.load_national_days(TODAY, count=2, idea_limit=2) is None


def test_nothing_is_requested_once_the_time_budget_is_spent(site, monkeypatch):
    monkeypatch.setattr(nd, "TOTAL_BUDGET_SECONDS", 0)

    assert nd.load_national_days(TODAY, count=2, idea_limit=2) is None
    assert site.calls == []


def test_a_page_that_parses_to_no_holidays_is_a_failure_not_an_empty_list(site):
    site.pages[nd.TODAY_URL] = "<html><body><h2>Redesigned!</h2><a href='/x-day/'>X Day</a></body></html>"

    assert nd.load_national_days(TODAY, count=2, idea_limit=2) is None


# ---- config -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config, key, default, maximum, expected",
    [
        (None, "count", 3, 5, 3), ({}, "count", 3, 5, 3), ({"count": 4}, "count", 3, 5, 4),
        ({"count": "2"}, "count", 3, 5, 2), ({"count": 0}, "count", 3, 5, 1), ({"count": -1}, "count", 3, 5, 1),
        ({"count": 99}, "count", 3, 5, 5), ({"count": "lots"}, "count", 3, 5, 3), ({"count": None}, "count", 3, 5, 3),
        ({"ideas": 9}, "ideas", 2, 4, 4),
    ],
)
def test_counts_are_clamped_with_sensible_defaults(config, key, default, maximum, expected):
    assert nd._resolve_int(config, key, default, maximum) == expected


def test_module_is_registered_online_with_a_validated_config():
    definition = module_registry.get_module("national_days")

    assert definition.label == "National Days"
    assert definition.offline is False and definition.icon == "confetti" and definition.category == "content"
    props = definition.config_schema["properties"]
    assert props["count"]["default"] == 3 and props["ideas"]["default"] == 2
    assert props["include_details"]["default"] is False and props["keep_page_order"]["default"] is False

    module_registry.validate_module_config("national_days", {})
    module_registry.validate_module_config("national_days", {"count": 5, "ideas": 4, "include_details": True})
    for bad in ({"count": 0}, {"count": 6}, {"ideas": 5}, {"count": "three"}, {"include_details": "yes"}):
        with pytest.raises(ValueError):
            module_registry.validate_module_config("national_days", bad)


def test_shipped_icon_exists():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert (root / "icons" / "regular" / "confetti.png").exists()


# ---- receipt ----------------------------------------------------------------------------


def test_receipt_prints_days_with_their_ideas_and_credits_the_source(site):
    calls = _print({"count": 2, "ideas": 2})

    assert calls[:3] == [("header", "NATIONAL DAYS", "confetti"), ("caption", "November 19, 2026"), ("line",)]
    assert calls[3:] == [
        ("bold", "Beaujolais Nouveau Day"),
        ("caption", "Ways to celebrate:"),
        ("body", "- Beaujolais Nouveau Day idea one"),
        ("body", "- Beaujolais Nouveau Day idea two"),
        ("feed", 1),
        ("bold", "National Have A Bad Day Day"),
        ("caption", "Ways to celebrate:"),
        ("body", "- National Have A Bad Day Day idea one"),
        ("body", "- National Have A Bad Day Day idea two"),
        ("line",),
        ("caption", "via nationaltoday.com"),
    ]


def test_receipt_uses_the_custom_module_name(site):
    assert _print(name="Party Time")[0] == ("header", "Party Time", "confetti")


def test_receipt_defaults_to_three_days_and_two_ideas_each(site):
    calls = _print()

    assert [c[1] for c in calls if c[0] == "bold"] == FUN_FIRST[:3]
    assert len([c for c in calls if c[0] == "body"]) == 6


def test_receipt_can_include_shortened_descriptions(site):
    site.pages["https://nationaltoday.com/beaujolais-nouveau-day/"] = _holiday_page(
        "Beaujolais Nouveau Day", [("Open a bottle", "word " * 60)]
    )

    calls = _print({"count": 1, "ideas": 1, "include_details": True})

    description = [c[1] for c in calls if c[0] == "caption"][1:-1]
    assert len(description) == 2  # "Ways to celebrate:" then the description
    assert description[1].endswith("...") and len(description[1]) <= nd.MAX_DETAIL_CHARS + 3


def test_receipt_keeps_site_order_when_asked(site):
    calls = _print({"count": 2, "keep_page_order": True})

    assert [c[1] for c in calls if c[0] == "bold"] == PAGE_ORDER[:2]


def test_receipt_lists_a_holiday_without_ideas_when_its_page_is_unavailable(site):
    site.pages["https://nationaltoday.com/beaujolais-nouveau-day/"] = 500

    calls = _print({"count": 1})

    assert ("bold", "Beaujolais Nouveau Day") in calls
    assert ("caption", "Ways to celebrate:") not in calls
    assert calls[-1] == ("caption", "via nationaltoday.com")


def test_receipt_says_so_when_the_site_cannot_be_reached(site):
    site.pages[nd.TODAY_URL] = requests.ConnectionError("down")

    calls = _print()

    assert ("body", "Couldn't load today's") in calls
    assert ("body", "internet connection.") in calls
    assert not [c for c in calls if c[0] in ("bold", "feed")]
    assert ("caption", "via nationaltoday.com") not in calls


def test_receipt_never_raises_on_a_broken_page(site):
    site.pages[nd.TODAY_URL] = "\x00<<<not html at all"

    calls = _print()

    assert ("body", "internet connection.") in calls


def test_shorten_cuts_on_a_word_boundary():
    assert nd._shorten("short text", 50) == "short text"
    assert nd._shorten("alpha beta gamma delta", 12) == "alpha beta..."
