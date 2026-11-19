"""Tests for scripts/build_history_data.py.

The Wikidata parts run against canned responses only; they have not been
exercised against the live service.
"""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_history_data.py"


@pytest.fixture(scope="module")
def bhd():
    spec = importlib.util.spec_from_file_location("build_history_data_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def _binding(qid, label, day, sl, occ=None):
    row = {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "itemLabel": {"value": label},
        "date": {"value": f"{day}T00:00:00Z"},
        "sl": {"value": str(sl)},
    }
    if occ:
        row["occ"] = {"value": f"http://www.wikidata.org/entity/{occ}"}
    return row


def _entry(m, d, y, c, s, t, src="curated"):
    return {"m": m, "d": d, "y": y, "c": c, "s": s, "t": t, "src": src}


# ---- text helpers --------------------------------------------------------


def test_shorten_prefers_sentence_boundary(bhd):
    text = "A" * 70 + ". " + "B" * 200

    assert bhd.shorten(text, limit=120) == "A" * 70 + "."


def test_shorten_leaves_short_text_alone_and_collapses_whitespace(bhd):
    assert bhd.shorten("A  short\n sentence.") == "A short sentence."


def test_duplicate_detection_matches_same_event_in_different_words(bhd):
    curated = _entry(11, 18, 1928, "film_tv", 100,
                     "Walt Disney's Steamboat Willie premieres in New York, the first fully synchronized sound cartoon.")
    classic = _entry(11, 18, 1928, "film_tv", 62,
                     "Release of the animated short Steamboat Willie, the first fully synchronized sound cartoon.",
                     src="classic")
    other_year = dict(classic, y=1929)
    unrelated = _entry(11, 18, 1928, "film_tv", 62, "A completely different thing happens.", src="classic")

    assert bhd.is_duplicate(curated, classic)
    assert not bhd.is_duplicate(curated, other_year)
    assert not bhd.is_duplicate(curated, unrelated)


# ---- loading -------------------------------------------------------------


def test_load_classic_keeps_only_calm_pop_culture_entries(bhd, tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"11": {"18": [
        "1928: Release of the animated short Steamboat Willie, the first fully synchronized sound cartoon.",
        "1991: After an 87-day siege, the Croatian city of Vukovar capitulates.",
        "1805: Beethoven's only opera, Fidelio, premieres in Vienna.",
        "1985: The first comic of Calvin and Hobbes is published in ten newspapers.",
    ]}}), encoding="utf-8")

    entries = bhd.load_classic(path)

    assert {(e["y"], e["c"]) for e in entries} == {(1928, "film_tv"), (1985, "culture")}
    assert all(e["src"] == "classic" and e["s"] < 70 for e in entries)


def test_shipped_curated_file_loads_with_valid_categories_and_dates(bhd):
    entries = bhd.load_curated()

    assert len(entries) >= 80
    assert {e["c"] for e in entries} <= bhd.CATEGORIES
    assert all(0 <= e["s"] <= 100 for e in entries)


def test_load_curated_rejects_unknown_category_and_impossible_date(bhd, tmp_path):
    bad_cat = tmp_path / "a.json"
    bad_cat.write_text(json.dumps({"entries": [{"m": 1, "d": 1, "y": 2000, "c": "politics", "s": 1, "t": "x"}]}))
    bad_date = tmp_path / "b.json"
    bad_date.write_text(json.dumps({"entries": [{"m": 2, "d": 30, "y": 2000, "c": "tech", "s": 1, "t": "x"}]}))

    with pytest.raises(ValueError):
        bhd.load_curated(bad_cat)
    with pytest.raises(ValueError):
        bhd.load_curated(bad_date)


# ---- merge ---------------------------------------------------------------


def test_merge_prefers_curated_over_duplicate_classic(bhd):
    curated = _entry(11, 18, 1928, "film_tv", 100,
                     "Walt Disney's Steamboat Willie premieres in New York, the first fully synchronized sound cartoon.")
    classic = _entry(11, 18, 1928, "film_tv", 62,
                     "Release of the animated short Steamboat Willie, the first fully synchronized sound cartoon.",
                     src="classic")

    merged = bhd.merge([classic, curated])

    assert [e["src"] for e in merged["11"]["18"]] == ["curated"]


def test_merge_caps_total_per_day_and_sorts_by_score(bhd):
    entries = [_entry(5, 5, 1970 + i, "tech", 40 + i, f"Distinct gadget number {i} ships worldwide.")
               for i in range(20)]

    day = bhd.merge(entries)["5"]["5"]

    assert len(day) == bhd.PER_DAY_CAP
    assert [e["s"] for e in day] == sorted((e["s"] for e in day), reverse=True)
    assert "birthday" not in bhd.CATEGORIES  # births have their own file


def test_write_themed_round_trips_through_the_module_loader(bhd, tmp_path, monkeypatch):
    from app.modules import history as history_module

    merged = bhd.merge([_entry(7, 4, 1971, "tech", 70, "Michael Hart types up a famous document.")])
    out = tmp_path / "history_themed.json"
    bhd.write_themed(out, merged, ["test"])

    monkeypatch.setattr(history_module, "THEMED_DB_PATH", out)
    assert history_module.get_themed_events_for_date(date(2026, 7, 4)) == [
        "1971: Michael Hart types up a famous document."
    ]
    assert json.loads(out.read_text(encoding="utf-8"))["meta"]["sources"] == ["test"]


# ---- Wikidata (canned responses only) ---------------------------------------


def test_queries_contain_expected_filters_and_ids(bhd):
    film = bhd.build_release_query("Q11424", 45, 1925, 1929)
    births = bhd.build_birth_query(60, 1950, 1954)

    assert "wdt:P31 wd:Q11424" in film and "?sl >= 45" in film
    assert "wikibase:timePrecision ?prec" in film and "?prec >= 11" in film
    assert '"1925-01-01T00:00:00Z"' in film and '"1930-01-01T00:00:00Z"' in film
    assert "wd:Q33999" in births and "wdt:P106 ?occ" in births and "?sl >= 60" in births


def test_year_windows_cover_range_without_overlap(bhd):
    assert bhd.year_windows(1920, 1932, 5) == [(1920, 1924), (1925, 1929), (1930, 1932)]


def test_run_sparql_retries_rate_limits_then_succeeds(bhd):
    responses = [
        FakeResponse(429, headers={"Retry-After": "3"}),
        FakeResponse(503),
        FakeResponse(200, {"results": {"bindings": [{"x": 1}]}}),
    ]
    sleeps = []

    def http_get(url, **kwargs):
        assert "paper-console-history-builder" in kwargs["headers"]["User-Agent"]
        return responses.pop(0)

    result = bhd.run_sparql("SELECT 1", "me@example.com", http_get=http_get, sleep=sleeps.append)

    assert result == [{"x": 1}]
    assert len(sleeps) == 2 and sleeps[0] >= 3


def test_run_sparql_raises_on_client_errors_and_after_exhausting_retries(bhd):
    with pytest.raises(RuntimeError, match="HTTP 400"):
        bhd.run_sparql("bad", "me@example.com", http_get=lambda *a, **k: FakeResponse(400, text="syntax"),
                       sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="after 2 attempts"):
        bhd.run_sparql("slow", "me@example.com", http_get=lambda *a, **k: FakeResponse(503),
                       sleep=lambda s: None, retries=2)


def test_cache_rows_become_entries_with_earliest_date_and_best_occupation(bhd, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    # Same film appears in two windows with different regional dates; earliest wins.
    (cache / "film_1925_1929.json").write_text(json.dumps(bhd.bindings_to_rows("film", [
        _binding("Q1", "Some Film", "1928-12-05", 90),
        _binding("Q2", "Q999", "1928-12-06", 90),  # unlabelled item -> skipped
    ])))
    (cache / "film_1930_1934.json").write_text(json.dumps(bhd.bindings_to_rows("film", [
        _binding("Q1", "Some Film", "1928-11-18", 90),
    ])))
    (cache / "game_2010_2014.json").write_text(json.dumps(bhd.bindings_to_rows("game", [
        _binding("Q3", "Some Game", "2011-11-18", 100),
    ])))
    (cache / "birth_1950_1954.json").write_text(json.dumps(bhd.bindings_to_rows("birth", [
        _binding("Q4", "Pat Star", "1952-03-03", 200, occ="Q639669"),  # musician
        _binding("Q4", "Pat Star", "1952-03-03", 200, occ="Q33999"),   # actor ranks higher
    ])))

    entries = {e["t"]: e for e in bhd.load_wikidata_cache(cache)}

    # births are not On This Day entries any more
    assert set(entries) == {"Some Film is released.", "Some Game is released."}
    film = entries["Some Film is released."]
    assert (film["m"], film["d"], film["y"], film["c"]) == (11, 18, 1928, "film_tv")
    assert entries["Some Game is released."]["c"] == "games"

    # the person comes back through the birthday loader, with the better occupation
    people = bhd.load_birth_rows(cache)
    assert people == [{"m": 3, "d": 3, "y": 1952, "n": "Pat Star", "o": "actor", "s": 200}]


def test_load_wikidata_cache_without_directory_is_empty(bhd, tmp_path):
    assert bhd.load_wikidata_cache(tmp_path / "missing") == []


def test_fetch_wikidata_skips_cached_windows_and_writes_new_ones(bhd, tmp_path):
    cache = tmp_path / "cache"
    calls = []

    def http_get(url, **kwargs):
        calls.append(kwargs["params"]["query"])
        return FakeResponse(200, {"results": {"bindings": [_binding("Q1", "X", "2000-01-02", 99)]}})

    mins = {"film": 45, "game": 30, "birth": 60}
    first = bhd.fetch_wikidata(cache, "me@example.com", mins, window_size=50, delay=0,
                               http_get=http_get, sleep=lambda s: None, this_year=2025)
    calls_after_first = len(calls)
    second = bhd.fetch_wikidata(cache, "me@example.com", mins, window_size=50, delay=0,
                                http_get=http_get, sleep=lambda s: None, this_year=2025)

    assert first == calls_after_first > 0
    assert second == 0 and len(calls) == calls_after_first
    assert any(p.name.startswith("birth_") for p in cache.glob("*.json"))


def test_check_wikidata_ids_reports_wrong_and_missing_labels(bhd):
    def http_get(url, **kwargs):
        ids = kwargs["params"]["ids"].split("|")
        entities = {}
        for wd_id in ids:
            label = bhd.WD_IDS[wd_id]
            if wd_id == "Q33999":
                label = "something else"
            if wd_id == "Q7889":
                continue  # missing entirely
            entities[wd_id] = {"labels": {"en": {"value": label}}}
        return FakeResponse(200, {"entities": entities})

    problems = bhd.check_wikidata_ids("me@example.com", http_get=http_get)

    assert any(p.startswith("Q33999") and "something else" in p for p in problems)
    assert any(p.startswith("Q7889") and "no English label" in p for p in problems)
    assert len(problems) == 2


def test_check_wikidata_ids_passes_when_labels_match(bhd):
    def http_get(url, **kwargs):
        ids = kwargs["params"]["ids"].split("|")
        return FakeResponse(200, {"entities": {i: {"labels": {"en": {"value": bhd.WD_IDS[i]}}} for i in ids}})

    assert bhd.check_wikidata_ids("me@example.com", http_get=http_get) == []


def test_cli_requires_contact_for_network_modes(bhd, capsys):
    assert bhd.main(["--fetch-wikidata"]) == 2
    assert bhd.main(["--check-ids"]) == 2
    assert "--contact is required" in capsys.readouterr().out


# ---- Wikidata scope filters (--wikidata-kinds / --*-since / --birth-occupations) ----


def _recording_http_get(calls):
    def http_get(url, **kwargs):
        calls.append(kwargs["params"]["query"])
        return FakeResponse(200, {"results": {"bindings": [_binding("Q1", "X", "2000-01-02", 99)]}})
    return http_get


MINS = {"film": 45, "game": 30, "birth": 60}


def test_fetch_wikidata_only_fetches_requested_kinds_and_start_years(bhd, tmp_path):
    cache = tmp_path / "cache"
    calls = []
    count = bhd.fetch_wikidata(
        cache, "me@example.com", MINS, window_size=5, delay=0, http_get=_recording_http_get(calls),
        sleep=lambda s: None, this_year=2026,
        kinds=["film", "birth"], since={"film": 1981, "birth": 1981},
    )

    names = sorted(p.name for p in cache.glob("*.json"))
    assert count == len(calls) == len(names) > 0
    assert not any(n.startswith("game_") for n in names)
    # earliest windows start exactly at the requested year; latest birth window ends 15 years ago
    assert "film_1981_1985.json" in names and not any(n.startswith("film_1980") for n in names)
    assert "birth_1981_1985.json" in names and "birth_2011_2011.json" in names
    assert not any(n.startswith("birth_2012") for n in names)


def test_fetch_wikidata_restricted_occupations_get_their_own_cache_files(bhd, tmp_path):
    cache = tmp_path / "cache"
    calls = []
    actors = [pair for pair in bhd.BIRTH_OCCUPATIONS if pair[1] == "actor"]
    kwargs = dict(window_size=50, delay=0, sleep=lambda s: None, this_year=2026,
                  kinds=["birth"], since={"birth": 1981})

    bhd.fetch_wikidata(cache, "me@example.com", MINS, http_get=_recording_http_get(calls),
                       occupations=actors, **kwargs)
    assert [p.name for p in cache.glob("*.json")] == ["birth_actor_1981_2011.json"]
    assert "wd:Q33999" in calls[0] and "wd:Q177220" not in calls[0]  # actors only, no singers

    # widening the occupation list later must fetch again rather than reuse the actor-only window
    before = len(calls)
    bhd.fetch_wikidata(cache, "me@example.com", MINS, http_get=_recording_http_get(calls), **kwargs)
    assert len(calls) == before + 1
    assert (cache / "birth_1981_2011.json").exists()


def test_load_wikidata_cache_applies_kind_year_and_occupation_filters(bhd, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "film_1975_1979.json").write_text(json.dumps(bhd.bindings_to_rows("film", [
        _binding("Q1", "Old Film", "1977-05-25", 99),
    ])))
    (cache / "film_1995_1999.json").write_text(json.dumps(bhd.bindings_to_rows("film", [
        _binding("Q2", "New Film", "1997-12-19", 99),
    ])))
    (cache / "game_2010_2014.json").write_text(json.dumps(bhd.bindings_to_rows("game", [
        _binding("Q3", "Some Game", "2011-11-18", 99),
    ])))
    (cache / "birth_1950_1954.json").write_text(json.dumps(bhd.bindings_to_rows("birth", [
        _binding("Q4", "Old Actor", "1952-03-03", 99, occ="Q33999"),
    ])))
    (cache / "birth_1985_1989.json").write_text(json.dumps(bhd.bindings_to_rows("birth", [
        _binding("Q5", "Young Actor", "1987-04-04", 99, occ="Q33999"),
        _binding("Q6", "Young Singer", "1988-05-05", 99, occ="Q177220"),
        _binding("Q7", "Young Both", "1989-06-06", 99, occ="Q177220"),
        _binding("Q7", "Young Both", "1989-06-06", 99, occ="Q33999"),
    ])))
    actors = [pair for pair in bhd.BIRTH_OCCUPATIONS if pair[1] == "actor"]

    since = {"film": 1981, "birth": 1981}
    texts = {e["t"] for e in bhd.load_wikidata_cache(
        cache, kinds=["film", "birth"], since=since, occupations=actors)}
    people = {(r["n"], r["o"]) for r in bhd.load_birth_rows(cache, since=since, occupations=actors)}

    assert texts == {"New Film is released."}
    assert people == {("Young Actor", "actor"), ("Young Both", "actor")}

    # defaults still load everything that was cached: 2 films + 1 game, and 4 people
    assert len(bhd.load_wikidata_cache(cache)) == 3
    assert len(bhd.load_birth_rows(cache)) == 4


def test_cli_scope_flags_parse_and_reject_unknown_values(bhd, capsys):
    args = bhd.parse_args(["--wikidata-kinds", "film, birth", "--film-since", "1981",
                           "--birth-since", "1981", "--birth-occupations", "Actor"])
    assert args.wikidata_kinds == ["film", "birth"]
    assert (args.film_since, args.game_since, args.birth_since) == (1981, 1970, 1981)
    assert args.birth_occupations == [("Q33999", "actor")]

    defaults = bhd.parse_args([])
    assert defaults.wikidata_kinds == ["film", "game", "birth"] and defaults.birth_occupations is None

    for bad in (["--wikidata-kinds", "film,books"], ["--birth-occupations", "plumber"]):
        with pytest.raises(SystemExit):
            bhd.parse_args(bad)
    assert "must be" in capsys.readouterr().err


def test_main_offline_build_honours_scope_filters(bhd, tmp_path, capsys):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "film_1975_1979.json").write_text(json.dumps(bhd.bindings_to_rows("film", [
        _binding("Q1", "Old Film", "1977-05-25", 99)])))
    (cache / "film_1995_1999.json").write_text(json.dumps(bhd.bindings_to_rows("film", [
        _binding("Q2", "New Film", "1997-12-19", 99)])))
    out = tmp_path / "out.json"

    assert bhd.main(["--no-classic", "--cache-dir", str(cache), "--output", str(out),
                     "--wikidata-kinds", "film", "--film-since", "1981"]) == 0
    texts = [e["t"] for days in json.loads(out.read_text())["events"].values()
             for entries in days.values() for e in entries]
    assert "New Film is released." in texts and "Old Film is released." not in texts


# ---- birthdays.json (the Birthdays module's data) --------------------------


def _person(m, d, y, n, s, o="actor"):
    return {"m": m, "d": d, "y": y, "n": n, "o": o, "s": s}


def test_build_birthdays_groups_by_day_ranks_by_reach_and_caps(bhd):
    rows = [_person(11, 19, 1962, "Jodie Foster", 120), _person(11, 19, 1961, "Meg Ryan", 110),
            _person(11, 19, 1983, "Adam Driver", 90), _person(11, 19, 1990, "Tie Breaker", 90),
            _person(2, 29, 1980, "Leap Actor", 55)]

    births = bhd.build_birthdays(rows, cap=3)

    assert [p["n"] for p in births["11"]["19"]] == ["Jodie Foster", "Meg Ryan", "Tie Breaker"]
    assert births["11"]["19"][0] == {"y": 1962, "n": "Jodie Foster", "o": "actor", "s": 120}
    assert births["2"]["29"][0]["n"] == "Leap Actor"
    assert bhd.birthdays_summary(births) == "4 people across 2 of 366 calendar days"


def test_write_birthdays_round_trips_through_the_module_loader(bhd, tmp_path, monkeypatch):
    from app.modules import birthdays as birthdays_module

    rows = [_person(11, 19, 1983, "Adam Driver", 90), _person(11, 19, 1962, "Jodie Foster", 120)]
    out = tmp_path / "birthdays.json"
    bhd.write_birthdays(out, bhd.build_birthdays(rows), ["test"])
    monkeypatch.setattr(birthdays_module, "BIRTHDAYS_DB_PATH", out)

    people = birthdays_module.get_birthdays_for_date(date(2026, 11, 19))

    assert [p["n"] for p in people] == ["Jodie Foster", "Adam Driver"]
    assert json.loads(out.read_text())["meta"]["sources"] == ["test"]


def test_main_writes_birthdays_file_and_keeps_births_out_of_history(bhd, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "birth_actor_1981_1985.json").write_text(json.dumps(bhd.bindings_to_rows("birth", [
        _binding("Q1", "Adam Driver", "1983-11-19", 90, occ="Q33999"),
        _binding("Q2", "Some Singer", "1984-11-19", 99, occ="Q177220"),
    ])))
    out, births_out = tmp_path / "themed.json", tmp_path / "birthdays.json"
    common = ["--no-classic", "--cache-dir", str(cache), "--output", str(out),
              "--birthdays-output", str(births_out), "--wikidata-kinds", "film,birth",
              "--birth-occupations", "actor"]

    assert bhd.main(common) == 0

    people = json.loads(births_out.read_text())["births"]["11"]["19"]
    assert [p["n"] for p in people] == ["Adam Driver"]  # the singer is outside the occupation filter
    themed = json.loads(out.read_text())["events"]
    assert all(e["c"] != "birthday" for days in themed.values() for es in days.values() for e in es)
    assert not any("Adam Driver" in e["t"] for days in themed.values() for es in days.values() for e in es)


def test_main_leaves_existing_birthdays_file_alone_when_cache_has_no_births(bhd, tmp_path, capsys):
    births_out = tmp_path / "birthdays.json"
    births_out.write_text('{"births": {}}', encoding="utf-8")

    assert bhd.main(["--no-classic", "--cache-dir", str(tmp_path / "empty"),
                     "--output", str(tmp_path / "themed.json"),
                     "--birthdays-output", str(births_out)]) == 0

    assert births_out.read_text(encoding="utf-8") == '{"births": {}}'
    assert "left as it is" in capsys.readouterr().out


# ---- positive-only filtering of classic entries ------------------------------


def test_classify_drops_entries_about_deaths_and_disasters_but_keeps_look_alike_words(bhd):
    died = "After a five-day search, the body of an American actress and singer is recovered from a lake, where she had drowned."
    train = "A CSX train rolls out of a yard with tank cars carrying flammable chemicals; the runaway train is later derailed."
    raiders = "The first of the Indiana Jones film franchise, Raiders of the Lost Ark, is released in theaters."
    dartmouth = "The Dartmouth workshop is the first conference on artificial intelligence."

    assert bhd.classify(died, 2020) is None
    assert bhd.classify(train, 2001) is None
    assert bhd.classify(raiders, 1981)[0] == "film_tv"  # "Raiders" is not a raid
    assert bhd.classify(dartmouth, 1956)[0] == "tech"  # a conference is not a political summit
