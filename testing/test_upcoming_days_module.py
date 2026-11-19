"""Tests for the Upcoming Days module (a week-ahead list read live from nationaltoday.com).

The fetching and parsing live in national_days.py and are tested in
test_national_days_module.py, whose fake site and page fixtures are reused here. This file
covers what belongs to the module itself: its settings, the receipt layout and what it says
when the site cannot be reached. No test touches the network.
"""

from datetime import date
from pathlib import Path

import pytest

from app import module_registry
from app.modules import national_days as nd
from app.modules import upcoming_days as ud
from testing.test_national_days_module import (  # noqa: F401  (fixtures are used by name)
    NEXT_DAYS,
    TODAY,
    _CapturePrinter,
    _add_date_pages,
    _date_urls,
    fresh_state,
    site,
)


def _print(config=None, name=None):
    printer = _CapturePrinter()
    ud.format_upcoming_days_receipt(printer, {"reference_date": "2026-11-19", **(config or {})}, name)
    return printer.calls


def _busy(first=20, last=26, per_date=3):
    """A fun day (or three) on each date from `first` to `last`, for the count tests."""
    return {date(2026, 11, d): [(f"fun-{d}-{n}", f"Fun {d} {n}", "Fun") for n in range(per_date)] for d in range(first, last + 1)}


def _bolds(calls):
    return [c[1] for c in calls if c[0] == "bold"]


# ---- registration and settings ----------------------------------------------------------


def test_module_is_registered_online_with_its_own_label_and_icon():
    definition = module_registry.get_module("upcoming_days")

    assert definition.label == "Upcoming Days"
    assert definition.offline is False and definition.category == "content"
    assert definition.icon == "calendar-star"
    root = Path(__file__).resolve().parent.parent
    assert (root / "icons" / "regular" / "calendar-star.png").exists()


def test_it_is_a_separate_module_from_national_days():
    assert module_registry.get_module("upcoming_days") is not module_registry.get_module("national_days")
    assert module_registry.get_module("upcoming_days").label != module_registry.get_module("national_days").label


def test_a_week_of_dates_and_two_days_each_are_the_defaults_with_limits_of_three_to_seven_and_one_to_five():
    props = module_registry.get_module("upcoming_days").config_schema["properties"]

    dates = props["count"]
    assert dates["title"] == "Dates to List"
    assert (dates["minimum"], dates["default"], dates["maximum"]) == (3, 7, 7)
    per_date = props["per_day"]
    assert per_date["title"] == "Days per Date"
    assert (per_date["minimum"], per_date["default"], per_date["maximum"]) == (1, 2, 5)
    assert dates["description"] and per_date["description"]


def test_only_the_three_listed_categories_get_a_skip_box_and_all_start_unticked():
    props = module_registry.get_module("upcoming_days").config_schema["properties"]

    boxes = {key: spec for key, spec in props.items() if spec["type"] == "boolean"}
    assert set(boxes) == {"exclude_arts_entertainment", "exclude_food_beverage", "exclude_fun"}
    assert all(spec["default"] is False and spec["title"].startswith("Skip ") for spec in boxes.values())
    assert set(props) == {"count", "per_day"} | set(boxes)
    assert len({spec["title"] for spec in props.values()}) == len(props)  # no two settings share a label


def test_the_config_is_validated():
    module_registry.validate_module_config("upcoming_days", {})
    module_registry.validate_module_config("upcoming_days", {"count": 3, "per_day": 1, "exclude_fun": True})
    module_registry.validate_module_config("upcoming_days", {"count": 7, "per_day": 5})
    for bad in (
        {"count": 2}, {"count": 8}, {"per_day": 0}, {"per_day": 6}, {"count": "week"}, {"exclude_fun": "yes"},
    ):
        with pytest.raises(ValueError):
            module_registry.validate_module_config("upcoming_days", bad)


def test_the_module_can_be_given_a_name_of_the_users_choosing(site):
    _add_date_pages(site)

    assert _print({"count": 3}, name="WEEK AHEAD")[0] == ("header", "WEEK AHEAD", "calendar-star")
    assert _print({"count": 3})[0] == ("header", "UPCOMING DAYS", "calendar-star")


# ---- receipt ----------------------------------------------------------------------------


def test_receipt_puts_a_rule_and_a_bold_date_above_each_dates_days_and_prints_only_their_names(site):
    _add_date_pages(site)

    calls = _print({"count": 3})

    assert calls == [
        ("header", "UPCOMING DAYS", "calendar-star"),
        ("caption", "Nov 20 - Nov 22, 2026"),
        ("line",),
        ("bold", "Fri, Nov 20"),
        ("body", "National Peanut Butter Fudge Day"),
        ("body", "National Absurdity Day"),
        ("line",),
        ("bold", "Sat, Nov 21"),
        ("body", "National Movie Day"),
        ("body", "National Stuffing Day"),
        ("line",),
        ("bold", "Sun, Nov 22"),
        ("body", "National Cranberry Relish Day"),
        ("body", "Go For A Ride Day"),
        ("line",),
        ("caption", "via nationaltoday.com"),
    ]


def test_it_starts_tomorrow_and_never_lists_today(site):
    _add_date_pages(site, _busy(19, 26))

    calls = _print({"count": 3})

    assert _bolds(calls) == ["Fri, Nov 20", "Sat, Nov 21", "Sun, Nov 22"]
    assert not any("Fun 19" in c[1] for c in calls if c[0] == "body")


def test_no_category_lines_are_printed_but_categories_still_filter(site):
    _add_date_pages(site)

    calls = _print({"count": 3, "per_day": 5})

    assert [c[1] for c in calls if c[0] == "caption"] == ["Nov 20 - Nov 22, 2026", "via nationaltoday.com"]
    shown = [c[1] for c in calls if c[0] == "body"]
    assert "Future Teachers of America Day" not in shown and "Transgender Day of Remembrance" not in shown
    assert "National Child's Day" not in shown
    assert "National Peanut Butter Fudge Day" in shown


def test_every_name_comes_after_the_bold_date_it_belongs_to(site):
    _add_date_pages(site)

    calls = _print({"count": 4, "per_day": 5})

    assert calls[2] == ("line",) and calls[3][0] == "bold"  # a rule, then a date: never a name first
    names_by_date = {}
    heading = None
    for call in calls:
        if call[0] == "bold":
            heading = call[1]
        elif call[0] == "body":
            names_by_date.setdefault(heading, []).append(call[1])
    assert list(names_by_date) == ["Fri, Nov 20", "Sat, Nov 21", "Sun, Nov 22", "Mon, Nov 23"]
    assert names_by_date["Mon, Nov 23"] == ["National Espresso Day"]


@pytest.mark.parametrize("dates", [3, 5, 7])
def test_a_rule_is_printed_directly_above_every_date_and_never_doubled(site, dates):
    _add_date_pages(site, _busy(per_date=2))

    calls = _print({"count": dates})

    headings = [i for i, call in enumerate(calls) if call[0] == "bold"]
    assert len(headings) == dates
    assert all(calls[i - 1] == ("line",) for i in headings)
    # One rule above each date, plus the one that closes the receipt before the credit line.
    assert calls.count(("line",)) == dates + 1
    assert not [i for i, call in enumerate(calls) if call == ("line",) and calls[i - 1] == ("line",)]


def test_a_week_is_listed_by_default(site):
    _add_date_pages(site, _busy())

    calls = _print()

    assert _bolds(calls) == [f"{day:%a}, Nov {day.day}" for day in (date(2026, 11, d) for d in range(20, 27))]
    assert ("caption", "Nov 20 - Nov 26, 2026") in calls


def test_the_caption_gives_the_span_across_a_month_and_year_end(site):
    _add_date_pages(site, {date(2026, 12, 31): [("a-fun-day", "A Fun Day", "Fun")], date(2027, 1, 1): [("b-fun-day", "B Fun Day", "Fun")]})

    printer = _CapturePrinter()
    ud.format_upcoming_days_receipt(printer, {"reference_date": "2026-12-30", "count": 3}, None)

    assert ("caption", "Dec 31 - Jan 1, 2027") in printer.calls


def test_a_single_date_gets_a_one_date_caption(site):
    _add_date_pages(site, {date(2026, 11, 21): [("national-movie-day", "National Movie Day", "Arts &amp; Entertainment")]})

    assert ("caption", "Nov 21, 2026") in _print({"count": 3})


@pytest.mark.parametrize("asked, dates", [(1, 3), (3, 3), (5, 5), (7, 7), (9, 7), ("many", 7), (0, 3)])
def test_the_number_of_dates_is_kept_between_three_and_seven(site, asked, dates):
    _add_date_pages(site, _busy(20, 30))

    assert len(_bolds(_print({"count": asked}))) == dates


@pytest.mark.parametrize("asked, per_date", [(1, 1), (3, 3), (5, 5), (9, 5), (0, 1), ("lots", 2)])
def test_the_number_of_days_under_each_date_is_kept_between_one_and_five(site, asked, per_date):
    _add_date_pages(site, _busy(per_date=7))

    calls = _print({"count": 3, "per_day": asked})

    assert len([c for c in calls if c[0] == "body"]) == 3 * per_date


def test_arts_days_print_before_food_days_before_fun_days_on_the_same_date(site):
    _add_date_pages(
        site,
        {date(2026, 11, 20): [("fun-day", "Fun Day", "Fun"), ("food-day", "Food Day", "Food &amp; Beverage"), ("arts-day", "Arts Day", "Arts &amp; Entertainment")]},
    )

    calls = _print({"count": 3, "per_day": 5})

    assert [c[1] for c in calls if c[0] == "body"] == ["Arts Day", "Food Day", "Fun Day"]


def test_a_date_with_nothing_arts_food_or_fun_is_left_out_and_the_next_date_takes_its_place(site):
    _add_date_pages(
        site,
        {
            date(2026, 11, 20): [("serious-day", "Serious Day", "Cause")],
            date(2026, 11, 21): [("a-fun-day", "A Fun Day", "Fun")],
            date(2026, 11, 22): [("b-fun-day", "B Fun Day", "Fun")],
            date(2026, 11, 23): [("c-fun-day", "C Fun Day", "Fun")],
            date(2026, 11, 24): [("d-fun-day", "D Fun Day", "Fun")],
        },
    )

    assert _bolds(_print({"count": 3})) == ["Sat, Nov 21", "Sun, Nov 22", "Mon, Nov 23"]


# ---- skip boxes -------------------------------------------------------------------------


def test_skip_checkboxes_leave_those_days_out(site):
    _add_date_pages(site)

    shown = [c[1] for c in _print({"count": 3, "per_day": 5, "exclude_fun": True}) if c[0] == "body"]

    assert "National Absurdity Day" not in shown and "Go For A Ride Day" not in shown  # the fun days
    assert "National Stuffing Day" in shown and "National Movie Day" in shown  # food and arts still listed


@pytest.mark.parametrize(
    "key, gone, kept",
    [
        ("exclude_arts_entertainment", "National Movie Day", "National Stuffing Day"),
        ("exclude_food_beverage", "National Stuffing Day", "National Movie Day"),
    ],
)
def test_each_skip_box_removes_only_its_own_category(site, key, gone, kept):
    _add_date_pages(site)

    shown = [c[1] for c in _print({"count": 3, "per_day": 5, key: True}) if c[0] == "body"]

    assert gone not in shown and kept in shown


def test_a_date_whose_only_days_are_skipped_is_left_out(site):
    _add_date_pages(site, _busy(20, 26, per_date=1) | {date(2026, 11, 21): [("national-movie-day", "National Movie Day", "Arts &amp; Entertainment")]})

    assert "Sat, Nov 21" in _bolds(_print({"count": 3}))
    assert "Sat, Nov 21" not in _bolds(_print({"count": 3, "exclude_arts_entertainment": True}))


def test_skipping_all_three_kinds_prints_the_nothing_matching_message_without_any_request(site):
    calls = _print({"exclude_arts_entertainment": True, "exclude_food_beverage": True, "exclude_fun": True})

    assert ("body", "Nothing matching in the") in calls
    assert ("caption", "via nationaltoday.com") not in calls
    assert site.urls == []


def test_old_saved_settings_from_the_national_days_module_are_harmless(site):
    _add_date_pages(site)
    old = {"count": 3, "ideas": 4, "show_upcoming": True, "upcoming_count": 5, "upcoming_per_day": 4, "exclude_cause": True}

    module_registry.validate_module_config("upcoming_days", old)

    assert _bolds(_print(old))[:1] == ["Fri, Nov 20"]


# ---- failures ---------------------------------------------------------------------------


def test_a_site_that_cannot_be_reached_says_so_and_prints_no_credit_line(site):
    calls = _print({"count": 3})

    assert calls == [
        ("header", "UPCOMING DAYS", "calendar-star"),
        ("line",),
        ("body", "Couldn't load upcoming"),
        ("body", "days. Check the internet"),
        ("body", "connection."),
    ]


def test_says_so_when_nothing_in_the_coming_days_matches(site):
    _add_date_pages(site, {date(2026, 11, d): [("serious-day", "Serious Day", "Cause")] for d in range(20, 30)})

    calls = _print({"count": 3})

    assert ("body", "Nothing matching in the") in calls
    assert ("body", f"next {nd.upcoming_lookahead(3)} days.") in calls
    assert ("caption", "via nationaltoday.com") not in calls


def test_the_nothing_matching_message_reports_the_real_lookahead_for_a_full_week(site):
    _add_date_pages(site, {date(2026, 11, d): [("serious-day", "Serious Day", "Cause")] for d in range(20, 31)})

    calls = _print({"count": 7})

    assert ("body", f"next {nd.upcoming_lookahead(7)} days.") in calls
    assert nd.upcoming_lookahead(7) == 10


def test_a_date_that_fails_to_load_does_not_lose_the_ones_that_did(site):
    _add_date_pages(site, {date(2026, 11, 20): [("a-fun-day", "A Fun Day", "Fun")], date(2026, 11, 22): [("b-fun-day", "B Fun Day", "Fun")]})

    calls = _print({"count": 3})

    assert _bolds(calls) == ["Fri, Nov 20", "Sun, Nov 22"]
    assert calls[-1] == ("caption", "via nationaltoday.com")


def test_the_receipt_never_raises_on_a_broken_page(site):
    for url in _date_urls(20, 21, 22, 23, 24, 25, 26, 27, 28, 29):
        site.pages[url] = "<html><body><h2>All holidays on November 20</h2><ul><li><a>"

    assert _print({"count": 3})[0] == ("header", "UPCOMING DAYS", "calendar-star")


# ---- politeness and caching -------------------------------------------------------------


def test_only_date_pages_are_requested_never_todays_list_or_holiday_pages(site):
    _add_date_pages(site, _busy())

    _print({"count": 7})

    assert site.urls == _date_urls(20, 21, 22, 23, 24, 25, 26)  # one request per date, in order, nothing else
    assert all(nd._is_date_url(url) for url in site.urls)
    assert nd.TODAY_URL not in site.urls


def test_a_second_print_on_the_same_day_makes_no_requests(site):
    _add_date_pages(site, _busy())
    first = _print({"count": 7})
    requests_made = len(site.calls)

    second = _print({"count": 7})

    assert second == first
    assert len(site.calls) == requests_made


def test_changing_the_skip_boxes_never_refetches_a_page_it_already_has(site):
    _add_date_pages(site, _busy())
    _print({"count": 7})
    requests_made = len(site.calls)

    _print({"count": 7, "exclude_fun": True})  # fewer dates match now, so later dates may be read too

    assert len(site.urls) == len(set(site.urls))  # but no date page is ever requested twice
    assert site.urls[:requests_made] == _date_urls(20, 21, 22, 23, 24, 25, 26)


def test_the_national_days_module_and_this_one_share_the_date_page_cache(site):
    _add_date_pages(site, _busy())
    _print({"count": 3})
    requests_made = len(site.calls)

    nd.load_upcoming_days(TODAY, 3, 2)

    assert len(site.calls) == requests_made


# ---- date headings ----------------------------------------------------------------------


def test_date_headings_show_the_weekday_and_an_unpadded_day():
    assert ud._heading(date(2026, 11, 20)) == "Fri, Nov 20"
    assert ud._heading(date(2027, 1, 3)) == "Sun, Jan 3"


# ---- National Days no longer has an upcoming section ------------------------------------


def test_national_days_has_no_upcoming_settings_any_more():
    props = module_registry.get_module("national_days").config_schema["properties"]

    assert not [key for key in props if "upcoming" in key]
    assert not [spec["title"] for spec in props.values() if "Upcoming" in spec["title"] or "Today:" in spec["title"]]


def test_a_saved_national_days_config_with_the_old_upcoming_settings_still_validates_and_prints_no_upcoming_section(site):
    _add_date_pages(site)
    old = {"count": 1, "ideas": 1, "show_upcoming": True, "upcoming_count": 5, "upcoming_per_day": 5}

    module_registry.validate_module_config("national_days", old)
    printer = _CapturePrinter()
    nd.format_national_days_receipt(printer, {"reference_date": "2026-11-19", **old}, None)

    assert ("bold", "UPCOMING DAYS") not in printer.calls
    assert not [url for url in site.urls if nd._is_date_url(url)]
