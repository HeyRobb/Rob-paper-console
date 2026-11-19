"""Tests for the History module's theme support and the shipped themed dataset."""

import calendar
import json
from datetime import date

import pytest
from PIL import Image

from app import module_registry
from app.history_rules import GRIM_PATTERN
from app.modules import history as history_module

# Days that have no entry at all in app/data/history.json (known gap in the
# classic data). Themed data may cover some of them; the rest print the
# "No historical records" message.
KNOWN_CLASSIC_GAPS = (
    {(3, d) for d in range(20, 32)} | {(4, 1), (4, 2), (4, 3)} | {(6, 16)}
)


class _CapturePrinter:
    PRINTER_WIDTH_DOTS = 384

    def __init__(self):
        self.calls = []

    def print_header(self, text, icon=None):
        self.calls.append(("header", text))

    def print_caption(self, text):
        self.calls.append(("caption", text))

    def print_line(self):
        self.calls.append(("line",))

    def print_body(self, text):
        self.calls.append(("body", text))

    def print_image(self, img):
        self.calls.append(("image",))


def _write_themed(tmp_path, events):
    path = tmp_path / "history_themed.json"
    path.write_text(json.dumps({"meta": {"version": 1}, "events": events}), encoding="utf-8")
    return path


@pytest.fixture
def themed_db(tmp_path, monkeypatch):
    path = _write_themed(
        tmp_path,
        {
            "11": {
                "18": [
                    {"y": 1985, "t": "Calvin and Hobbes debuts.", "c": "culture", "s": 62},
                    {"y": 2011, "t": "Minecraft 1.0 is released.", "c": "games", "s": 100},
                    {"y": 1928, "t": "Steamboat Willie premieres.", "c": "film_tv", "s": 100},
                    {"y": 1969, "t": "Some singer is born.", "c": "birthday", "s": 40},
                    {"y": 1971, "t": "Intel ships a chip.", "c": "tech", "s": 80},
                ]
            }
        },
    )
    monkeypatch.setattr(history_module, "THEMED_DB_PATH", path)
    return path


def test_pop_theme_returns_pop_categories_best_first_and_ignores_legacy_birthdays(themed_db):
    # The fixture still holds a "birthday" entry like an older build of the data
    # file would; famous birthdays belong to the Birthdays module now.
    events = history_module.get_themed_events_for_date(date(2026, 11, 18), history_module.THEME_POP)

    assert events == [
        "2011: Minecraft 1.0 is released.",
        "1928: Steamboat Willie premieres.",
        "1971: Intel ships a chip.",
        "1985: Calvin and Hobbes debuts.",
    ]


def test_categories_argument_limits_genres(themed_db):
    events = history_module.get_themed_events_for_date(
        date(2026, 11, 18), history_module.THEME_POP, categories={"games", "tech"}
    )

    assert events == ["2011: Minecraft 1.0 is released.", "1971: Intel ships a chip."]


def test_skip_toggles_map_to_remaining_categories():
    assert history_module._resolve_categories(None) == history_module.ALL_GENRE_CATEGORIES
    assert history_module._resolve_categories({"count": 3}) == history_module.ALL_GENRE_CATEGORIES
    # "exclude_birthdays" was a toggle in an earlier build; a saved config that
    # still has it must be accepted and simply ignored.
    assert history_module._resolve_categories(
        {"exclude_games": True, "exclude_birthdays": True, "exclude_music": False}
    ) == {"film_tv", "music", "tech", "culture", "science"}
    assert history_module._resolve_categories(
        {key: True for key, _category, _title in history_module.GENRES}
    ) == frozenset()


def test_receipt_honors_skip_toggles(themed_db, monkeypatch):
    captured = {}

    def _fake_timeline(width, items, item_height, font):  # noqa: ARG001
        captured["items"] = items
        return Image.new("1", (8, 8), 1)

    monkeypatch.setattr(history_module, "draw_timeline_image", _fake_timeline)

    history_module.format_history_receipt(
        _CapturePrinter(),
        {
            "reference_date": "2026-11-18",
            "count": 10,
            "exclude_games": True,
            "exclude_birthdays": True,
            "exclude_culture": True,
        },
        None,
    )

    assert [item["text"] for item in captured["items"]] == [
        "Steamboat Willie premieres.",
        "Intel ships a chip.",
    ]


def test_skipping_every_genre_falls_back_to_calm_classic(themed_db, monkeypatch):
    monkeypatch.setattr(
        history_module,
        "get_events_for_date",
        lambda d: ["1991: A siege ends.", "1985: A popular cartoon airs its first episode."],
    )
    everything_skipped = {key: True for key, _category, _title in history_module.GENRES}
    categories = history_module._resolve_categories(everything_skipped)

    events = history_module.get_events_for_theme(
        date(2026, 11, 18), history_module.THEME_POP, categories
    )

    assert events == ["1985: A popular cartoon airs its first episode."]


def test_empty_themed_day_falls_back_to_calm_classic(themed_db, monkeypatch):
    monkeypatch.setattr(
        history_module,
        "get_events_for_date",
        lambda d: [
            "1991: After an 87-day siege, the city capitulates to besieging forces.",
            "1985: A popular cartoon airs its first episode.",
            "1960: An earthquake kills hundreds.",
        ],
    )

    events = history_module.get_events_for_theme(date(2026, 1, 2), history_module.THEME_POP)

    assert events == ["1985: A popular cartoon airs its first episode."]


def test_grim_classic_entries_are_never_printed_even_when_nothing_else_is_left(themed_db, monkeypatch):
    grim = ["1991: A siege ends.", "1960: An earthquake kills hundreds."]
    monkeypatch.setattr(history_module, "get_events_for_date", lambda d: list(grim))

    assert history_module.get_events_for_theme(date(2026, 1, 2), history_module.THEME_POP) == []


def test_missing_themed_file_falls_back_to_classic_without_error(tmp_path, monkeypatch):
    monkeypatch.setattr(history_module, "THEMED_DB_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(history_module, "get_events_for_date", lambda d: ["1999: A calm event."])

    assert history_module.get_events_for_theme(date(2026, 1, 2)) == ["1999: A calm event."]


def test_corrupt_themed_file_falls_back_to_classic(tmp_path, monkeypatch):
    bad = tmp_path / "history_themed.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(history_module, "THEMED_DB_PATH", bad)
    monkeypatch.setattr(history_module, "get_events_for_date", lambda d: ["1999: A calm event."])

    assert history_module.get_events_for_theme(date(2026, 1, 2)) == ["1999: A calm event."]


def test_classic_theme_keeps_only_positive_entries_and_ignores_themed_file(themed_db, monkeypatch):
    monkeypatch.setattr(
        history_module,
        "get_events_for_date",
        lambda d: [
            "1066: A battle happens.",
            "1943: Nazis liquidate a concentration camp, murdering thousands.",
            "1972: A famous actor dies at 80.",
            "1990: A man is killed in an accident.",
            "1969: The first university on the moon opens.",
        ],
    )

    events = history_module.get_events_for_theme(date(2026, 11, 18), history_module.THEME_CLASSIC)

    assert events == ["1969: The first university on the moon opens."]


def test_classic_theme_with_nothing_positive_shows_a_pop_entry_instead(themed_db, monkeypatch):
    monkeypatch.setattr(
        history_module, "get_events_for_date", lambda d: ["1066: A battle happens.", "1943: Thousands are murdered."]
    )

    events = history_module.get_events_for_theme(date(2026, 11, 18), history_module.THEME_CLASSIC)

    assert events[0] == "2011: Minecraft 1.0 is released."  # themed entries, never the grim ones
    assert all("battle" not in e and "murdered" not in e for e in events)


def test_classic_theme_with_nothing_positive_and_no_themed_entries_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(history_module, "THEMED_DB_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(history_module, "get_events_for_date", lambda d: ["1066: A battle happens."])

    assert history_module.get_events_for_theme(date(2026, 11, 18), history_module.THEME_CLASSIC) == []


@pytest.mark.parametrize(
    "text",
    [
        "The Holocaust: Nazis liquidate Janowska concentration camp, murdering at least 6,000 Jews.",
        "A gunman kills five and injures 17 at a nightclub.",
        "He was murdered in his home.",
        "The actor died at the age of 80.",
        "Two people were killed when the bridge collapsed.",
        "Wartime rationing ends.",
        "Quisling seizes power in Norway.",
        "Tanganyika becomes independent from Britain.",
        "The Space Shuttle program: NASA grounds the Space Shuttle fleet.",
        "A hurricane makes landfall.",
        "He was sentenced to ten years in prison for fraud.",
        "Georges Carpentier knocked out Joe Beckett.",
    ],
)
def test_grim_pattern_blocks_deaths_violence_war_disaster_crime_and_politics(text):
    assert GRIM_PATTERN.search(text), text


@pytest.mark.parametrize(
    "text",
    [
        "The Beatles release Abbey Road.",
        "Beatnik poets gather in San Francisco.",
        "NASA launches the MAVEN probe to Mars.",
        "Apollo 11 begins a new era of lunar exploration.",
        "Victoria Falls Bridge opens to traffic.",
        "Citizen Kane premieres in New York City.",
        "Ford introduces the moving assembly line.",
        "A bank accepts its first deposit.",
        "Cy Young throws a perfect game at the Huntington Avenue Grounds.",
        "Stanford University opens its doors in California.",
    ],
)
def test_grim_pattern_does_not_swallow_positive_words_that_merely_look_similar(text):
    assert not GRIM_PATTERN.search(text), (text, GRIM_PATTERN.search(text).group(0))


def test_themed_cache_reloads_when_file_changes(tmp_path, monkeypatch):
    path = _write_themed(tmp_path, {"1": {"1": [{"y": 2000, "t": "First.", "c": "tech", "s": 50}]}})
    monkeypatch.setattr(history_module, "THEMED_DB_PATH", path)
    assert history_module.get_themed_events_for_date(date(2026, 1, 1)) == ["2000: First."]

    path.write_text(
        json.dumps({"events": {"1": {"1": [{"y": 2001, "t": "Second.", "c": "tech", "s": 50}]}}}),
        encoding="utf-8",
    )
    # Force a distinct mtime even on coarse filesystems.
    import os

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    assert history_module.get_themed_events_for_date(date(2026, 1, 1)) == ["2001: Second."]


def test_receipt_defaults_to_pop_theme_and_takes_top_entries_for_reference_date(themed_db, monkeypatch):
    captured = {}

    def _fake_timeline(width, items, item_height, font):  # noqa: ARG001
        captured["items"] = items
        return Image.new("1", (8, 8), 1)

    monkeypatch.setattr(history_module, "draw_timeline_image", _fake_timeline)
    printer = _CapturePrinter()

    history_module.format_history_receipt(
        printer, {"reference_date": "2026-11-18", "count": 2}, "On This Day"
    )

    assert captured["items"] == [
        {"year": 2011, "text": "Minecraft 1.0 is released."},
        {"year": 1928, "text": "Steamboat Willie premieres."},
    ]
    assert ("caption", "November 18, 2026") in printer.calls


def test_receipt_unknown_theme_value_uses_default(themed_db, monkeypatch):
    captured = {}

    def _fake_timeline(width, items, item_height, font):  # noqa: ARG001
        captured["items"] = items
        return Image.new("1", (8, 8), 1)

    monkeypatch.setattr(history_module, "draw_timeline_image", _fake_timeline)

    history_module.format_history_receipt(
        _CapturePrinter(), {"reference_date": "2026-11-18", "count": 1, "theme": ""}, None
    )

    assert captured["items"][0]["text"] == "Minecraft 1.0 is released."


def test_config_schema_offers_theme_enum_and_validates():
    schema = module_registry.get_module("history").config_schema
    theme = schema["properties"]["theme"]

    assert theme["enum"] == history_module.THEMES
    assert theme["default"] == history_module.DEFAULT_THEME == "Pop culture & tech"

    module_registry.validate_module_config(
        "history", {"theme": "Classic history", "count": 2, "exclude_music": True}
    )
    module_registry.validate_module_config("history", {"count": 1})  # existing saved configs keep working
    module_registry.validate_module_config("history", {"exclude_birthdays": True})  # removed toggle: still accepted
    with pytest.raises(ValueError):
        module_registry.validate_module_config("history", {"theme": "Politics only"})
    with pytest.raises(ValueError):
        module_registry.validate_module_config("history", {"exclude_music": "yes"})


def test_config_schema_has_one_skip_checkbox_per_genre_defaulting_to_unchecked():
    properties = module_registry.get_module("history").config_schema["properties"]
    toggles = {k: v for k, v in properties.items() if k.startswith("exclude_")}

    assert set(toggles) == {key for key, _category, _title in history_module.GENRES}
    assert len(toggles) == 6
    assert "exclude_birthdays" not in toggles
    assert history_module.ALL_GENRE_CATEGORIES == {
        "film_tv", "music", "games", "tech", "culture", "science"
    }
    for toggle in toggles.values():
        assert toggle["type"] == "boolean" and toggle["title"].startswith("Skip ")
        # The settings form renders an unset checkbox as unchecked, so the
        # default must be False or the form would disagree with the module.
        assert toggle["default"] is False


# ---- shipped data -------------------------------------------------------


def test_shipped_themed_file_is_well_formed():
    raw = json.loads(history_module.THEMED_DB_PATH.read_text(encoding="utf-8"))
    valid_categories = {"film_tv", "music", "games", "tech", "culture"}

    for month, days in raw["events"].items():
        for day, entries in days.items():
            date(2024, int(month), int(day))  # must be a real calendar day
            for entry in entries:
                assert entry["c"] in valid_categories
                assert isinstance(entry["y"], int) and entry["t"].strip()
                assert 0 <= entry["s"] <= 100
                assert len(entry["t"]) <= 200


def test_shipped_data_gives_the_user_nov_18_examples_not_vukovar():
    events = history_module.get_events_for_theme(date(2026, 11, 18), history_module.THEME_POP)
    joined = " ".join(events)

    assert "Minecraft" in joined
    assert "Steamboat Willie" in joined
    assert "Vukovar" not in joined


def test_every_day_has_something_to_print_except_known_classic_gaps():
    empty = set()
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2024, month)[1] + 1):
            if not history_module.get_events_for_theme(date(2024, month, day)):
                empty.add((month, day))

    assert empty <= KNOWN_CLASSIC_GAPS, sorted(empty - KNOWN_CLASSIC_GAPS)


def test_classic_theme_never_prints_grim_entries_in_shipped_data():
    classic = json.loads(history_module.LOCAL_DB_PATH.read_text(encoding="utf-8"))
    printed_anything = 0
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2024, month)[1] + 1):
            events = history_module.get_events_for_theme(date(2024, month, day), history_module.THEME_CLASSIC)
            classic_today = set(classic.get(str(month), {}).get(str(day), []))
            for event in events:
                # a pop-culture fallback entry can carry a title like "Die Hard"; classic ones cannot
                if event in classic_today:
                    assert not GRIM_PATTERN.search(event), (month, day, event)
            printed_anything += bool(events)

    assert printed_anything >= 366 - len(KNOWN_CLASSIC_GAPS)


def test_shipped_classic_history_for_nov_19_has_no_holocaust_or_killing():
    events = history_module.get_events_for_theme(date(2026, 11, 19), history_module.THEME_CLASSIC)
    joined = " ".join(events).lower()

    assert events
    for word in ("holocaust", "janowska", "killed", "kills", "murder", "died", "dies", "massacre"):
        assert word not in joined


# ---- category-first selection ------------------------------------------------


def test_pick_events_visits_each_genre_before_repeating_one():
    candidates = [
        ("2001: Film A.", "film_tv"), ("2002: Film B.", "film_tv"), ("2003: Film C.", "film_tv"),
        ("2004: Film D.", "film_tv"), ("2005: Film E.", "film_tv"),
        ("1999: A console ships.", "games"),
        ("1976: A company is founded.", "tech"),
    ]
    import random

    for seed in range(40):
        picked = history_module.pick_events(candidates, 3, random.Random(seed))
        genre_of = {text: genre for text, genre in candidates}
        genres = {genre_of[text] for text in picked}
        assert genres == {"film_tv", "games", "tech"}, (seed, picked)


def test_pick_events_without_rng_is_deterministic_and_round_robin_by_score():
    candidates = [
        ("2011: Game.", "games"), ("2010: Film one.", "film_tv"), ("2009: Film two.", "film_tv"),
        ("1990: Gadget.", "tech"),
    ]

    assert history_module.pick_events(candidates, 2) == ["2011: Game.", "2010: Film one."]
    assert history_module.pick_events(candidates, 4) == [text for text, _ in candidates]
    # once the single-entry genres are used up, the film genre supplies the rest
    assert history_module.pick_events(candidates, 3) == ["2011: Game.", "2010: Film one.", "1990: Gadget."]


def test_pick_events_handles_edges_and_untagged_classic_entries():
    import random

    assert history_module.pick_events([], 3, random.Random(1)) == []
    assert history_module.pick_events([("1999: X.", None)], 0, random.Random(1)) == []
    assert history_module.pick_events([("1999: X.", None)], -2, random.Random(1)) == []
    classic = [(f"19{n}: Calm event {n}.", None) for n in range(10, 16)]
    picked = history_module.pick_events(classic, 3, random.Random(5))
    assert len(picked) == len(set(picked)) == 3 and set(picked) <= {t for t, _ in classic}


def test_receipt_without_reference_date_mixes_genres(themed_db, monkeypatch):
    captured = {}

    def _fake_timeline(width, items, item_height, font):  # noqa: ARG001
        captured["items"] = items
        return Image.new("1", (8, 8), 1)

    monkeypatch.setattr(history_module, "draw_timeline_image", _fake_timeline)
    monkeypatch.setattr(history_module, "current_date", lambda: date(2026, 11, 18))

    seen = set()
    for _ in range(25):
        history_module.format_history_receipt(_CapturePrinter(), {"count": 4}, None)
        seen.add(tuple(sorted(item["text"] for item in captured["items"])))

    # four genres are available, so four events always means one from each
    assert seen == {tuple(sorted([
        "Calvin and Hobbes debuts.", "Minecraft 1.0 is released.",
        "Steamboat Willie premieres.", "Intel ships a chip.",
    ]))}


def test_science_genre_is_selectable_and_skippable(tmp_path, monkeypatch):
    path = _write_themed(tmp_path, {"2": {"18": [
        {"y": 1930, "t": "Pluto is discovered.", "c": "science", "s": 70},
        {"y": 1985, "t": "Some film is released.", "c": "film_tv", "s": 60},
    ]}})
    monkeypatch.setattr(history_module, "THEMED_DB_PATH", path)
    day = date(2026, 2, 18)

    assert history_module.get_events_for_theme(day) == ["1930: Pluto is discovered.", "1985: Some film is released."]
    only_film = history_module._resolve_categories({"exclude_science": True})
    assert history_module.get_events_for_theme(day, categories=only_film) == ["1985: Some film is released."]
