"""Tests for the Celebrity Birthdays module and its shipped dataset."""

import calendar
import json
import os
from datetime import date

import pytest
from PIL import Image

from app import module_registry
from app.modules import birthdays as birthdays_module


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

    def print_image(self, img):
        self.calls.append(("image",))


def _write_births(tmp_path, births):
    path = tmp_path / "birthdays.json"
    path.write_text(json.dumps({"meta": {"version": 1}, "births": births}), encoding="utf-8")
    return path


@pytest.fixture
def births_db(tmp_path, monkeypatch):
    path = _write_births(
        tmp_path,
        {
            "11": {
                "19": [
                    {"y": 1983, "n": "Adam Driver", "o": "actor", "s": 90},
                    {"y": 1962, "n": "Jodie Foster", "o": "actor", "s": 120},
                    {"y": 1961, "n": "Meg Ryan", "o": "actor", "s": 110},
                    {"y": 1975, "n": "Some Comedian", "o": "comedian", "s": 60},
                ]
            }
        },
    )
    monkeypatch.setattr(birthdays_module, "BIRTHDAYS_DB_PATH", path)
    return path


@pytest.fixture
def timeline_items(monkeypatch):
    captured = {}

    def _fake_timeline(width, items, item_height, font):  # noqa: ARG001
        captured["items"] = items
        return Image.new("1", (8, 8), 1)

    monkeypatch.setattr(birthdays_module, "draw_timeline_image", _fake_timeline)
    return captured


# ---- lookup ----------------------------------------------------------------


def test_birthdays_are_ordered_by_reach_and_limit_keeps_the_most_known(births_db):
    everyone = birthdays_module.get_birthdays_for_date(date(2026, 11, 19))
    top_two = birthdays_module.get_birthdays_for_date(date(2026, 11, 19), limit=2)

    assert [p["n"] for p in everyone] == ["Jodie Foster", "Meg Ryan", "Adam Driver", "Some Comedian"]
    assert [p["n"] for p in top_two] == ["Jodie Foster", "Meg Ryan"]


def test_day_without_entries_returns_empty(births_db):
    assert birthdays_module.get_birthdays_for_date(date(2026, 11, 20)) == []


def test_missing_or_corrupt_file_returns_empty_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(birthdays_module, "BIRTHDAYS_DB_PATH", tmp_path / "nope.json")
    assert birthdays_module.get_birthdays_for_date(date(2026, 11, 19)) == []

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(birthdays_module, "BIRTHDAYS_DB_PATH", broken)
    assert birthdays_module.get_birthdays_for_date(date(2026, 11, 19)) == []


def test_malformed_people_are_skipped(tmp_path, monkeypatch):
    path = _write_births(
        tmp_path,
        {"1": {"2": [{"y": 1990, "n": "Good Person", "o": "actor", "s": 70},
                     {"y": "1990", "n": "Year Is Text", "s": 99},
                     {"y": 1991, "n": "", "s": 99},
                     "not a dict"]}},
    )
    monkeypatch.setattr(birthdays_module, "BIRTHDAYS_DB_PATH", path)

    assert [p["n"] for p in birthdays_module.get_birthdays_for_date(date(2026, 1, 2))] == ["Good Person"]


def test_file_is_reloaded_when_it_changes(births_db):
    assert len(birthdays_module.get_birthdays_for_date(date(2026, 11, 19))) == 4

    births_db.write_text(
        json.dumps({"births": {"11": {"19": [{"y": 2000, "n": "Only One", "o": "actor", "s": 50}]}}}),
        encoding="utf-8",
    )
    stat = births_db.stat()
    os.utime(births_db, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))  # make the mtime differ

    assert [p["n"] for p in birthdays_module.get_birthdays_for_date(date(2026, 11, 19))] == ["Only One"]


# ---- config ------------------------------------------------------------------


@pytest.mark.parametrize(
    "config, expected",
    [
        (None, 5), ({}, 5), ({"count": 3}, 3), ({"count": "7"}, 7),
        ({"count": 0}, 1), ({"count": -4}, 1), ({"count": 99}, 20),
        ({"count": "many"}, 5), ({"count": None}, 5),
    ],
)
def test_count_is_clamped_with_a_sensible_default(config, expected):
    assert birthdays_module._resolve_count(config) == expected


def test_module_is_registered_and_validates_its_config():
    definition = module_registry.get_module("birthdays")

    assert definition.label == "Celebrity Birthdays"
    assert definition.offline is True and definition.icon == "cake"
    assert definition.config_schema["properties"]["count"]["default"] == 5

    module_registry.validate_module_config("birthdays", {})
    module_registry.validate_module_config("birthdays", {"count": 12})
    with pytest.raises(ValueError):
        module_registry.validate_module_config("birthdays", {"count": 0})
    with pytest.raises(ValueError):
        module_registry.validate_module_config("birthdays", {"count": 21})
    with pytest.raises(ValueError):
        module_registry.validate_module_config("birthdays", {"count": "five"})


# ---- receipt -------------------------------------------------------------------


def test_receipt_prints_top_people_oldest_birth_year_first(births_db, timeline_items):
    printer = _CapturePrinter()

    birthdays_module.format_birthdays_receipt(
        printer, {"reference_date": "2026-11-19", "count": 3}, None
    )

    assert printer.calls[0] == ("header", "BIRTHDAYS", "cake")
    assert printer.calls[1] == ("caption", "November 19, 2026")
    assert printer.calls[-1] == ("image",)
    assert timeline_items["items"] == [
        {"year": 1961, "text": "Meg Ryan"},
        {"year": 1962, "text": "Jodie Foster"},
        {"year": 1983, "text": "Adam Driver"},
    ]


def test_receipt_names_the_occupation_only_when_it_is_not_actor(births_db, timeline_items):
    birthdays_module.format_birthdays_receipt(
        _CapturePrinter(), {"reference_date": "2026-11-19", "count": 10}, "Born Today"
    )

    texts = {item["text"] for item in timeline_items["items"]}
    assert "Some Comedian (comedian)" in texts
    assert "Adam Driver" in texts


def test_receipt_uses_custom_module_name_and_default_count_of_five(births_db, timeline_items):
    printer = _CapturePrinter()

    birthdays_module.format_birthdays_receipt(printer, {"reference_date": "2026-11-19"}, "Born Today")

    assert printer.calls[0][1] == "Born Today"
    assert len(timeline_items["items"]) == 4  # only four people exist for the day


def test_receipt_says_so_when_nobody_is_listed(births_db, timeline_items):
    printer = _CapturePrinter()

    birthdays_module.format_birthdays_receipt(printer, {"reference_date": "2026-11-20"}, None)

    assert ("body", "No famous birthdays") in printer.calls
    assert ("body", "found for today.") in printer.calls
    assert ("image",) not in printer.calls and "items" not in timeline_items


def test_receipt_survives_a_missing_data_file(tmp_path, monkeypatch):
    monkeypatch.setattr(birthdays_module, "BIRTHDAYS_DB_PATH", tmp_path / "nope.json")
    printer = _CapturePrinter()

    birthdays_module.format_birthdays_receipt(printer, {"reference_date": "2026-11-19"}, None)

    assert ("body", "No famous birthdays") in printer.calls


# ---- shipped data --------------------------------------------------------------

SHIPPED = birthdays_module.BIRTHDAYS_DB_PATH


@pytest.mark.skipif(not SHIPPED.exists(), reason="app/data/birthdays.json has not been generated yet")
def test_shipped_birthdays_file_is_well_formed():
    raw = json.loads(SHIPPED.read_text(encoding="utf-8"))

    for month, days in raw["births"].items():
        for day, people in days.items():
            date(2024, int(month), int(day))  # must be a real calendar day
            assert len(people) <= 15
            ranks = [p["s"] for p in people]
            assert ranks == sorted(ranks, reverse=True)
            for person in people:
                assert person["n"].strip() and isinstance(person["y"], int)
                assert 1850 <= person["y"] <= date.today().year
                assert isinstance(person["s"], int) and person["s"] > 0


@pytest.mark.skipif(not SHIPPED.exists(), reason="app/data/birthdays.json has not been generated yet")
def test_shipped_birthdays_cover_most_calendar_days():
    empty = [
        (month, day)
        for month in range(1, 13)
        for day in range(1, calendar.monthrange(2024, month)[1] + 1)
        if not birthdays_module.get_birthdays_for_date(date(2024, month, day))
    ]

    assert len(empty) <= 10, empty
