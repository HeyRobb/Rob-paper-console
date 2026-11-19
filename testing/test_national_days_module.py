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


def _holiday_page(name, ideas, heading=None):
    """A holiday page shaped like the real ones: each h2 section is an ol of li > div > h3 + p.

    The text is invented; only the structure follows the live pages, where the ideas
    section's h2 is worded per page ("How To Celebrate X", "X Activities", ...).
    """
    heading = heading or f"How To Celebrate {name}"
    items = "".join(f"<li><div><h3>{title}</h3><p>{text}</p></div></li>" for title, text in ideas)
    return f"""
    <html><body><main>
      <h1>{name} - November 19, 2026</h1><p>A short intro about {name}.</p>
      <h2>Top Brands for {name}</h2><ol><li><div><h3>Some Brand</h3><p>Sells things.</p></div></li></ol>
      <h2>How Businesses Can Celebrate {name}</h2><p>Run a promotion.</p>
      <h2>{name} FAQ s</h2><h3>How to celebrate {name}?</h3><p>Any way you like.</p>
      <h2>{heading}</h2><ol>{items}</ol>
      <h2>5 Facts About {name}</h2><ol><li><div><h3>Not an idea</h3><p>Just a fact.</p></div></li></ol>
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


@pytest.mark.parametrize(
    "heading",
    [
        "How To Celebrate Apple Cider Day",
        "How to Observe International Journalist Day",
        "National Camp Day Activities",
        "Beaujolais Nouveau Day Activities",
        "Ways to Celebrate Pi Day",
        "How To Honor Teachers Day",
    ],
)
def test_every_known_wording_of_the_ideas_heading_is_found(heading):
    ideas = nd.parse_celebrate_ideas(_holiday_page("Some Day", _ideas_for("Some"), heading=heading))

    assert [i["title"] for i in ideas] == ["Some idea one", "Some idea two", "Some idea three"]


@pytest.mark.parametrize(
    "heading",
    [
        "How Businesses Can Celebrate Some Day",
        "Top Brands for Some Day",
        "Platform Guide for Some Day",
        "Social Media Tips for Some Day",
        "Why We Love Some Day",
        "5 Facts About Some Day",
        "Some Day FAQ s",
        "Some Day timeline",
    ],
)
def test_other_sections_with_the_same_shape_are_never_taken_for_the_ideas(heading):
    html = f"<h2>{heading}</h2><ol><li><div><h3>Not an idea</h3><p>Some text.</p></div></li></ol>"

    assert nd.parse_celebrate_ideas(html) == []


def test_an_faq_question_in_an_h3_is_not_taken_for_the_ideas_section():
    html = "<h2>Some Day FAQ s</h2><h3>How to celebrate Some Day?</h3><p>However you like.</p>"

    assert nd.parse_celebrate_ideas(html) == []


def test_the_ideas_section_stops_at_the_next_h2_even_when_it_has_many_items():
    ideas = [(f"Idea {n}", f"Text {n}.") for n in range(1, 7)]

    found = nd.parse_celebrate_ideas(_holiday_page("Some Day", ideas, heading="Some Day Activities"))

    assert [i["title"] for i in found] == [f"Idea {n}" for n in range(1, 7)]
    assert found[-1]["text"] == "Text 6."


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
    assert props["keep_page_order"]["default"] is False
    assert "include_details" not in props and props["ideas"]["maximum"] == 10

    module_registry.validate_module_config("national_days", {})
    module_registry.validate_module_config("national_days", {"count": 5, "ideas": 10})
    module_registry.validate_module_config("national_days", {"exclude_fun": True, "exclude_cause": False})
    for bad in (
        {"count": 0}, {"count": 6}, {"ideas": 11}, {"ideas": 0}, {"count": "three"},
        {"exclude_cause": "yes"},
    ):
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
        ("caption", "Nov 19 - Food & Beverage"),
        ("caption", "Ways to celebrate:"),
        ("body", "- Beaujolais Nouveau Day idea one"),
        ("body", "- Beaujolais Nouveau Day idea two"),
        ("feed", 1),
        ("bold", "National Have A Bad Day Day"),
        ("caption", "Nov 19 - Fun"),
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


def test_receipt_prints_only_idea_titles_never_descriptions(site):
    site.pages["https://nationaltoday.com/beaujolais-nouveau-day/"] = _holiday_page(
        "Beaujolais Nouveau Day", [("Open a bottle", "A long description that must stay off the paper.")]
    )

    calls = _print({"count": 1, "ideas": 1})

    assert [c for c in calls if c[0] == "caption"] == [
        ("caption", "November 19, 2026"),
        ("caption", "Nov 19 - Food & Beverage"),
        ("caption", "Ways to celebrate:"),
        ("caption", "via nationaltoday.com"),
    ]
    assert ("body", "- Open a bottle") in calls
    assert not [c for c in calls if "description" in str(c)]


def test_a_saved_config_that_still_has_the_old_description_option_works_and_prints_no_descriptions(site):
    config = {"count": 1, "ideas": 1, "include_details": True}

    module_registry.validate_module_config("national_days", config)
    calls = _print(config)

    assert len([c for c in calls if c[0] == "body"]) == 1


def test_up_to_ten_ideas_per_day_are_printed_when_the_page_has_them(site):
    site.pages["https://nationaltoday.com/beaujolais-nouveau-day/"] = _holiday_page(
        "Beaujolais Nouveau Day", [(f"Idea {n}", f"Text {n}.") for n in range(1, 13)]
    )

    ten = _print({"count": 1, "ideas": 10})
    too_many = _print({"count": 1, "ideas": 99})  # out-of-range values are clamped, never an error

    assert [c[1] for c in ten if c[0] == "body"] == [f"- Idea {n}" for n in range(1, 11)]
    assert len([c for c in too_many if c[0] == "body"]) == 10


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


# ---- category checkboxes ------------------------------------------------------------------


def test_there_is_one_unticked_skip_checkbox_for_each_known_category():
    props = module_registry.get_module("national_days").config_schema["properties"]
    boxes = {key: spec for key, spec in props.items() if key.startswith("exclude_")}

    assert len(boxes) == len(nd.CATEGORIES) == 12
    assert {label for label, _key, _title in nd.CATEGORIES} == nd.CATEGORY_LABELS
    assert set(boxes) == {key for _label, key, _title in nd.CATEGORIES}
    assert all(spec["type"] == "boolean" and spec["default"] is False for spec in boxes.values())
    assert all(spec["title"].startswith("Skip ") for spec in boxes.values())


def test_ticked_boxes_map_to_the_category_labels_they_skip():
    config = {"exclude_fun": True, "exclude_food_beverage": 1, "exclude_cause": False, "count": 3}

    assert nd._skipped_categories(config) == {"fun", "food & beverage"}
    assert nd._skipped_categories({}) == frozenset()
    assert nd._skipped_categories(None) == frozenset()


def test_skipped_categories_are_left_out_before_the_count_is_applied(site):
    days = nd.load_national_days(
        TODAY, count=3, idea_limit=1, skip_categories=frozenset({"fun", "food & beverage"})
    )

    assert [d["name"] for d in days] == ["National Camp Day", "Great American Smokeout", "International Men's Day"]
    # Skipped days cost nothing: their pages are never requested.
    assert "https://nationaltoday.com/beaujolais-nouveau-day/" not in site.urls
    assert "https://nationaltoday.com/have-a-bad-day-day/" not in site.urls


def test_changing_the_checkboxes_does_not_refetch_the_list(site):
    nd.load_national_days(TODAY, count=2, idea_limit=1)
    nd.load_national_days(TODAY, count=2, idea_limit=1, skip_categories=frozenset({"fun"}))

    assert site.urls.count(nd.TODAY_URL) == 1


def test_skipping_every_category_returns_an_empty_list_not_a_failure(site):
    days = nd.load_national_days(TODAY, count=5, idea_limit=1, skip_categories=frozenset(nd.CATEGORY_LABELS))

    assert days == []  # [] means "loaded, nothing left"; None would mean "could not load"
    assert site.urls == [nd.TODAY_URL]


def test_days_without_a_category_label_are_never_skipped(site):
    site.pages[nd.TODAY_URL] = (
        "<h2>All holidays today</h2><ul>"
        '<li><a href="/mystery-day/">Mystery Day</a></li>'
        '<li><span>Fun</span><a href="/fun-day/">Fun Day</a></li></ul>'
    )
    site.pages["https://nationaltoday.com/mystery-day/"] = _holiday_page("Mystery Day", _ideas_for("Mystery"))

    days = nd.load_national_days(TODAY, count=5, idea_limit=1, skip_categories=frozenset(nd.CATEGORY_LABELS))

    assert [d["name"] for d in days] == ["Mystery Day"]


def test_receipt_leaves_out_the_categories_whose_boxes_are_ticked(site):
    calls = _print(
        {"count": 5, "exclude_fun": True, "exclude_food_beverage": True, "exclude_special_interest": True}
    )

    assert [c[1] for c in calls if c[0] == "bold"] == ["Great American Smokeout", "International Men's Day"]


def test_receipt_says_nothing_matches_when_every_box_is_ticked(site):
    calls = _print({key: True for _label, key, _title in nd.CATEGORIES})

    assert ("body", "No national days today") in calls
    assert ("body", "match your settings.") in calls
    assert ("body", "internet connection.") not in calls  # the site loaded fine; that is not a failure
    assert not [c for c in calls if c[0] == "bold"]


# ---- print order, and the date and category printed under each name -------------------------

# Every category the site uses plus one unlabeled day, listed in the opposite of print order.
ORDER_PAGE = (
    "<h2>All holidays today</h2><ul>"
    + _entry("federal-day", "Federal Day", "Federal")
    + _entry("cause-day", "Cause Day", "Cause")
    + _entry("health-day", "Health Day", "Health")
    + '<li><a href="/mystery-day/">Mystery Day</a></li>'
    + _entry("religious-day", "Religious Day", "Religious")
    + _entry("appreciation-day", "Appreciation Day", "Appreciation")
    + _entry("cultural-day", "Cultural Day", "Cultural")
    + _entry("relationship-day", "Relationship Day", "Relationships")
    + _entry("special-interest-day", "Special Interest Day", "Special Interest")
    + _entry("animal-day", "Animal Day", "Animals")
    + _entry("fun-day", "Fun Day", "Fun")
    + _entry("food-day", "Food Day", "Food &amp; Beverage")
    + _entry("arts-day", "Arts Day", "Arts &amp; Entertainment")
    + "</ul>"
)


def test_default_order_runs_from_arts_and_food_to_awareness_days_with_unlabeled_days_between(site):
    site.pages[nd.TODAY_URL] = ORDER_PAGE

    days = nd.load_national_days(TODAY, count=20, idea_limit=1)

    assert [d["name"] for d in days] == [
        "Arts Day", "Food Day", "Fun Day", "Animal Day", "Special Interest Day", "Relationship Day",
        "Cultural Day", "Appreciation Day", "Religious Day", "Mystery Day", "Health Day", "Cause Day",
        "Federal Day",
    ]


def test_special_interest_days_no_longer_jump_ahead_of_arts_food_and_fun_days(site):
    # The real November 19 list, trimmed: the site lists these alphabetically, so special
    # interest days used to win the first slots just by coming first.
    site.pages[nd.TODAY_URL] = (
        "<h2>All holidays today</h2><ul>"
        + _entry("great-american-smokeout", "Great American Smokeout", "Health")
        + _entry("beaujolais-nouveau-day", "Beaujolais Nouveau Day", "Food &amp; Beverage")
        + _entry("international-journalist-day", "International Journalist Day", "Special Interest")
        + _entry("national-camp-day", "National Camp Day", "Special Interest")
        + _entry("national-carbonated-beverage-with-caffeine-day", "National Carbonated Beverage With Caffeine Day", "Food &amp; Beverage")
        + _entry("national-play-monopoly-day", "National Play Monopoly Day", "Arts &amp; Entertainment")
        + "</ul>"
    )

    days = nd.load_national_days(TODAY, count=3, idea_limit=1, skip_categories=frozenset({"health", "cause"}))

    assert [d["name"] for d in days] == [
        "National Play Monopoly Day", "Beaujolais Nouveau Day", "National Carbonated Beverage With Caffeine Day",
    ]


def test_days_in_the_same_category_keep_the_sites_order(site):
    site.pages[nd.TODAY_URL] = (
        "<h2>All holidays today</h2><ul>"
        + _entry("b-day", "B Day", "Fun") + _entry("a-day", "A Day", "Fun") + _entry("c-day", "C Day", "Fun")
        + "</ul>"
    )

    days = nd.load_national_days(TODAY, count=3, idea_limit=1)

    assert [d["name"] for d in days] == ["B Day", "A Day", "C Day"]


def test_each_day_prints_its_date_and_category_under_its_name(site):
    calls = _print({"count": 3, "ideas": 1})

    lines = [c[1] for c in calls if c[0] == "caption" and c[1].startswith("Nov 19")]
    assert lines == ["Nov 19 - Food & Beverage", "Nov 19 - Fun", "Nov 19 - Special Interest"]
    for position, call in enumerate(calls):
        if call[0] == "bold":
            assert calls[position + 1][0] == "caption" and calls[position + 1][1].startswith("Nov 19")


def test_a_day_without_a_category_label_prints_only_its_date(site):
    site.pages[nd.TODAY_URL] = '<h2>All holidays today</h2><ul><li><a href="/mystery-day/">Mystery Day</a></li></ul>'

    calls = _print({"count": 1})

    assert ("bold", "Mystery Day") in calls
    assert ("caption", "Nov 19") in calls


def test_date_line_uses_the_target_date_and_does_not_pad_the_day_number():
    from datetime import date as _date

    assert nd._day_line(_date(2026, 3, 5), "arts & entertainment") == "Mar 5 - Arts & Entertainment"
    assert nd._day_line(_date(2026, 3, 5), None) == "Mar 5"
    assert nd._category_title("food & beverage") == "Food & Beverage"


def test_the_ideas_setting_warns_that_most_days_only_list_three():
    spec = module_registry.get_module("national_days").config_schema["properties"]["ideas"]

    assert "3" in spec["description"]


# ---- category wording ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Fun", "fun"), ("Food & Beverage", "food & beverage"), ("Relationship", "relationship"),
        ("Relationships", "relationship"), ("Animals", "animal"), ("Animal", "animal"),
        ("Religious", "religious"), ("Nope", None), ("", None), (None, None),
    ],
)
def test_category_wording_is_recognised_in_singular_or_plural(text, expected):
    assert nd._as_category(text) == expected


def test_relationship_animal_and_religious_days_print_their_category(site):
    site.pages[nd.TODAY_URL] = (
        "<h2>All holidays today</h2><ul>"
        + _entry("pay-back-your-parents-day", "Pay Back Your Parents Day", "Relationship")
        + "</ul>"
    )

    calls = _print({"count": 1})

    assert ("caption", "Nov 19 - Relationship") in calls


# ---- upcoming days ---------------------------------------------------------------------------

NEXT_DAYS = {
    date(2026, 11, 20): [
        ("future-teachers-of-america-day", "Future Teachers of America Day", "Special Interest"),
        ("national-absurdity-day", "National Absurdity Day", "Fun"),
        ("national-peanut-butter-fudge-day", "National Peanut Butter Fudge Day", "Food &amp; Beverage"),
        ("transgender-day-of-remembrance", "Transgender Day of Remembrance", "Cause"),
        ("national-childs-day", "National Child's Day", "Relationship"),
    ],
    date(2026, 11, 21): [
        ("world-hello-day", "World Hello Day", "Cultural"),
        ("national-stuffing-day", "National Stuffing Day", "Food &amp; Beverage"),
        ("national-movie-day", "National Movie Day", "Arts &amp; Entertainment"),
    ],
    date(2026, 11, 22): [
        ("go-for-a-ride-day", "Go For A Ride Day", "Fun"),
        ("national-cranberry-relish-day", "National Cranberry Relish Day", "Food &amp; Beverage"),
    ],
    date(2026, 11, 23): [
        ("national-espresso-day", "National Espresso Day", "Food &amp; Beverage"),
        ("fibonacci-day", "Fibonacci Day", "Special Interest"),
    ],
}


def _date_page(label, entries):
    """A date page shaped like the real /november-20/ page, with decoy links outside the list."""
    items = "".join(_entry(slug, name, category) for slug, name, category in entries)
    return (
        "<html><body><header><nav><a href='/today/'>Today</a><a href='/tomorrow/'>Tomorrow</a></nav></header>"
        f"<main><h1>{label} Holidays</h1><p>Intro <a href='/intro-only-day/'>Intro Only Day</a></p>"
        f"<h2>All holidays on {label}</h2><ul>{items}"
        "<li><a href='/november-30/'>Another date</a></li></ul>"
        "<aside><a href='/sidebar-only-day/'>Sidebar Only Day</a></aside>"
        f"<h2>About {label}</h2><p><a href='/about-only-day/'>About Only Day</a></p>"
        "<h2>International holidays on November 20</h2><p><a href='/international-only-day/'>Intl</a></p>"
        "</main><footer><a href='/footer-only-day/'>Footer Only Day</a></footer></body></html>"
    )


def _add_date_pages(site, pages=None):
    for day, entries in (pages or NEXT_DAYS).items():
        site.pages[nd._date_url(day)] = _date_page(f"{nd.MONTH_SLUGS[day.month - 1].title()} {day.day}", entries)


def _date_urls(*days):
    return [nd._date_url(date(2026, 11, d)) for d in days]


def test_a_date_page_is_read_like_the_daily_page_and_ignores_everything_outside_its_list():
    holidays = nd.parse_today_page(_date_page("November 20", NEXT_DAYS[date(2026, 11, 20)]))

    assert [(h["name"], h["category"]) for h in holidays] == [
        ("Future Teachers of America Day", "special interest"),
        ("National Absurdity Day", "fun"),
        ("National Peanut Butter Fudge Day", "food & beverage"),
        ("Transgender Day of Remembrance", "cause"),
        ("National Child's Day", "relationship"),
    ]


@pytest.mark.parametrize(
    "day, url",
    [
        (date(2026, 11, 20), "https://nationaltoday.com/november-20/"),
        (date(2026, 1, 5), "https://nationaltoday.com/january-5/"),
        (date(2026, 12, 31), "https://nationaltoday.com/december-31/"),
        (date(2028, 2, 29), "https://nationaltoday.com/february-29/"),
    ],
)
def test_date_page_addresses_use_the_month_name_and_an_unpadded_day(day, url):
    assert nd._date_url(day) == url
    assert nd._is_date_url(url)


def test_links_to_other_dates_are_never_holidays_but_their_pages_may_be_fetched():
    assert nd._holiday_url("/november-21/") is None
    assert nd._holiday_url("https://nationaltoday.com/february-29/") is None
    assert nd._holiday_url("/november-19-cool-day/") is not None  # not a bare date
    assert not nd._is_date_url("https://nationaltoday.com/november-20/?x=1")
    assert not nd._is_date_url("https://evil.example/november-20/")
    assert not nd._is_date_url("https://nationaltoday.com/november-20/international/")
    assert not nd._is_date_url("https://nationaltoday.com/national-pie-day/")


def test_upcoming_starts_tomorrow_and_keeps_only_arts_food_and_fun_in_date_order(site):
    _add_date_pages(site)

    days = nd.load_upcoming_days(TODAY, count=4)

    assert [(d["name"], d["date"].day, d["category"]) for d in days] == [
        ("National Peanut Butter Fudge Day", 20, "food & beverage"),
        ("National Absurdity Day", 20, "fun"),
        ("National Movie Day", 21, "arts & entertainment"),
        ("National Stuffing Day", 21, "food & beverage"),
    ]
    assert all(d["url"].startswith("https://nationaltoday.com/") for d in days)


def test_upcoming_stops_requesting_pages_once_it_has_enough(site):
    _add_date_pages(site)

    enough_from_tomorrow = nd.load_upcoming_days(TODAY, count=2)
    assert len(enough_from_tomorrow) == 2 and site.urls == _date_urls(20)

    nd._reset_cache()
    site.calls.clear()
    nd.load_upcoming_days(TODAY, count=3)
    assert site.urls == _date_urls(20, 21)


def test_upcoming_never_returns_more_than_asked_for(site):
    _add_date_pages(site)

    assert len(nd.load_upcoming_days(TODAY, count=3)) == 3
    assert len(nd.load_upcoming_days(TODAY, count=1)) == 1


def test_upcoming_respects_the_skip_checkboxes(site):
    _add_date_pages(site)

    days = nd.load_upcoming_days(TODAY, count=5, skip_categories=frozenset({"fun", "cause"}))

    assert "fun" not in {d["category"] for d in days}
    assert [d["name"] for d in days][:2] == ["National Peanut Butter Fudge Day", "National Movie Day"]


def test_upcoming_makes_no_requests_when_arts_food_and_fun_are_all_skipped(site):
    _add_date_pages(site)

    days = nd.load_upcoming_days(TODAY, count=3, skip_categories=frozenset(nd.UPCOMING_CATEGORIES))

    assert days == [] and site.urls == []


def test_upcoming_looks_a_week_ahead_at_most_then_gives_up_with_an_empty_list(site):
    boring = {date(2026, 11, d): [("serious-day", "Serious Day", "Cause")] for d in range(20, 28)}
    _add_date_pages(site, boring)

    assert nd.load_upcoming_days(TODAY, count=3) == []
    assert site.urls == _date_urls(20, 21, 22, 23, 24, 25, 26)  # seven dates, never the eighth


def test_upcoming_returns_what_it_found_when_a_later_date_page_fails(site):
    _add_date_pages(site)
    site.pages[nd._date_url(date(2026, 11, 21))] = 500

    days = nd.load_upcoming_days(TODAY, count=4)

    assert [d["date"].day for d in days] == [20, 20, 22, 22]


def test_upcoming_is_none_when_no_date_page_can_be_loaded_and_is_retried_next_time(site):
    assert nd.load_upcoming_days(TODAY, count=3) is None  # the fake site has no date pages at all

    _add_date_pages(site)
    assert len(nd.load_upcoming_days(TODAY, count=3)) == 3


def test_a_date_page_without_the_holiday_list_heading_counts_as_failed(site):
    site.pages[nd._date_url(date(2026, 11, 20))] = "<h2>Redesigned</h2><a href='/some-day/'>Some Day</a>"

    assert nd.load_upcoming_days(TODAY, count=3, lookahead=1) is None


def test_upcoming_pages_are_cached_until_their_date_has_passed(site):
    _add_date_pages(site)
    nd.load_upcoming_days(TODAY, count=3)
    fetched = len(site.calls)

    nd.load_upcoming_days(TODAY, count=3)
    assert len(site.calls) == fetched  # second print costs nothing

    nd.load_upcoming_days(date(2026, 11, 20), count=2)  # tomorrow became today: its page is dropped
    assert date(2026, 11, 20) not in nd._upcoming_cache
    assert date(2026, 11, 21) in nd._upcoming_cache


def test_the_same_holiday_listed_on_two_dates_is_only_returned_once(site):
    twice = [("long-running-fun-day", "Long Running Fun Day", "Fun")]
    _add_date_pages(site, {date(2026, 11, 20): twice, date(2026, 11, 21): twice + NEXT_DAYS[date(2026, 11, 21)]})

    names = [d["name"] for d in nd.load_upcoming_days(TODAY, count=5)]

    assert names.count("Long Running Fun Day") == 1


def test_upcoming_rolls_over_month_and_year_ends(site):
    site.pages[nd._date_url(date(2026, 12, 31))] = _date_page("December 31", [("a-fun-day", "A Fun Day", "Fun")])
    site.pages[nd._date_url(date(2027, 1, 1))] = _date_page("January 1", [("b-fun-day", "B Fun Day", "Fun")])

    days = nd.load_upcoming_days(date(2026, 12, 30), count=2)

    assert site.urls == ["https://nationaltoday.com/december-31/", "https://nationaltoday.com/january-1/"]
    assert [(d["date"].month, d["date"].day) for d in days] == [(12, 31), (1, 1)]


def test_upcoming_requests_are_modest_and_stay_on_site(site):
    _add_date_pages(site)

    nd.load_upcoming_days(TODAY, count=5)

    for call in site.calls:
        parts = urlparse(call["url"])
        assert parts.scheme == "https" and parts.hostname == "nationaltoday.com"
        assert not parts.path.startswith(nd.DISALLOWED_PREFIXES)
        assert call["headers"]["User-Agent"] == nd.USER_AGENT
        assert call["timeout"] == nd.REQUEST_TIMEOUT


def test_upcoming_stops_when_its_time_budget_is_used_up(site, monkeypatch):
    _add_date_pages(site)
    monkeypatch.setattr(nd, "UPCOMING_BUDGET_SECONDS", 0)

    assert nd.load_upcoming_days(TODAY, count=3) is None
    assert site.urls == []


def test_upcoming_is_never_fetched_unless_asked_for(site):
    _add_date_pages(site)

    calls = _print({"count": 1})

    assert ("bold", "UPCOMING DAYS") not in calls
    assert not [url for url in site.urls if nd._is_date_url(url)]


def test_receipt_sets_the_upcoming_section_apart_and_prints_only_name_date_and_category(site):
    _add_date_pages(site)

    calls = _print({"count": 1, "ideas": 1, "show_upcoming": True})

    start = calls.index(("bold", "UPCOMING DAYS"))
    assert calls[start - 2 : start] == [("feed", 1), ("line",)]
    assert calls[start:] == [
        ("bold", "UPCOMING DAYS"),
        ("body", "National Peanut Butter Fudge Day"),
        ("caption", "Nov 20 - Food & Beverage"),
        ("body", "National Absurdity Day"),
        ("caption", "Nov 20 - Fun"),
        ("body", "National Movie Day"),
        ("caption", "Nov 21 - Arts & Entertainment"),
        ("line",),
        ("caption", "via nationaltoday.com"),
    ]
    assert [c for c in calls if c == ("caption", "via nationaltoday.com")] == [("caption", "via nationaltoday.com")]


@pytest.mark.parametrize("asked, printed", [(1, 3), (3, 3), (4, 4), (5, 5), (9, 5), ("many", 3)])
def test_upcoming_count_is_kept_between_three_and_five(site, asked, printed):
    busy = {date(2026, 11, d): [(f"fun-{d}-{n}", f"Fun {d} {n}", "Fun") for n in range(3)] for d in range(20, 27)}
    _add_date_pages(site, busy)

    calls = _print({"count": 1, "show_upcoming": True, "upcoming_count": asked})

    start = calls.index(("bold", "UPCOMING DAYS"))
    assert len([c for c in calls[start:] if c[0] == "body"]) == printed


def test_a_failed_upcoming_load_says_so_without_losing_todays_days(site):
    calls = _print({"count": 1, "show_upcoming": True})

    assert ("bold", "Beaujolais Nouveau Day") in calls
    assert ("bold", "UPCOMING DAYS") in calls
    assert ("body", "Couldn't load upcoming days.") in calls
    assert calls[-1] == ("caption", "via nationaltoday.com")


def test_upcoming_says_so_when_nothing_in_the_next_week_matches(site):
    _add_date_pages(site, {date(2026, 11, d): [("serious-day", "Serious Day", "Cause")] for d in range(20, 27)})

    calls = _print({"count": 1, "show_upcoming": True})

    assert ("body", "Nothing matching in the") in calls
    assert ("body", "next 7 days.") in calls


def test_skip_checkboxes_also_leave_days_out_of_the_upcoming_list(site):
    _add_date_pages(site)

    calls = _print({"count": 1, "show_upcoming": True, "exclude_fun": True})

    start = calls.index(("bold", "UPCOMING DAYS"))
    assert not [c for c in calls[start:] if c[0] == "caption" and c[1].endswith("- Fun")]


def test_upcoming_still_prints_when_every_one_of_todays_days_is_skipped(site):
    _add_date_pages(site, {date(2026, 11, 21): [("national-movie-day", "National Movie Day", "Arts &amp; Entertainment")]})

    calls = _print(
        {
            "count": 3, "show_upcoming": True, "exclude_health": True, "exclude_cause": True,
            "exclude_food_beverage": True, "exclude_fun": True, "exclude_special_interest": True,
        }
    )

    assert ("body", "No national days today") in calls
    assert ("body", "National Movie Day") in calls
    assert calls[-1] == ("caption", "via nationaltoday.com")


def test_nothing_from_the_site_means_no_credit_line(site):
    calls = _print({key: True for _label, key, _title in nd.CATEGORIES} | {"show_upcoming": True})

    assert ("body", "No national days today") in calls
    assert ("body", "Nothing matching in the") in calls
    assert ("caption", "via nationaltoday.com") not in calls


def test_the_upcoming_settings_are_off_by_default_and_limited_to_three_to_five():
    props = module_registry.get_module("national_days").config_schema["properties"]

    assert props["show_upcoming"]["default"] is False
    assert (props["upcoming_count"]["minimum"], props["upcoming_count"]["default"], props["upcoming_count"]["maximum"]) == (3, 3, 5)
    module_registry.validate_module_config("national_days", {"show_upcoming": True, "upcoming_count": 5})
    for bad in ({"upcoming_count": 2}, {"upcoming_count": 6}, {"show_upcoming": "yes"}):
        with pytest.raises(ValueError):
            module_registry.validate_module_config("national_days", bad)


def test_no_credit_line_when_todays_list_is_all_skipped_and_upcoming_cannot_load(site):
    calls = _print(
        {
            "count": 3, "show_upcoming": True, "exclude_health": True, "exclude_cause": True,
            "exclude_food_beverage": True, "exclude_fun": True, "exclude_special_interest": True,
        }
    )

    assert ("body", "Couldn't load upcoming days.") in calls
    assert ("caption", "via nationaltoday.com") not in calls  # nothing from the site was printed
