#!/usr/bin/env python3
"""Build app/data/history_themed.json for the History ("On This Day") module.

Sources, merged and de-duplicated per calendar day:
  1. app/data/history.json - classic entries, tagged by app.history_rules.classify()
  2. scripts/history_sources/curated_milestones.json - hand-checked milestones
  3. Wikidata (optional, needs internet) - films, video games, TV series, game
     consoles, space launches, company foundings and famous inventions or
     discoveries, ranked by how many Wikipedia language editions cover them (a
     fame proxy).

Famous birthdays are written to a second file, app/data/birthdays.json, for the
Birthdays module (same ranking, up to --births-per-day people per day). They are
kept out of history_themed.json so On This Day is not crowded by them.

Offline build (no network needed):

    ./.venv/bin/python scripts/build_history_data.py

Add Wikidata (run on a machine with normal internet access, not the sandbox):

    ./.venv/bin/python scripts/build_history_data.py --check-ids --contact you@example.com
    ./.venv/bin/python scripts/build_history_data.py --fetch-wikidata --contact you@example.com

Limit the Wikidata step to what you want (here: films since 1981 plus actors born
since 1981, nothing else), then build:

    ./.venv/bin/python scripts/build_history_data.py --fetch-wikidata --contact you@example.com \\
        --wikidata-kinds film,birth --film-since 1981 --birth-since 1981 \\
        --birth-occupations actor --min-sitelinks-birth 40

Add the newer kinds (TV, consoles, space launches, companies, inventions) on top of
what is already cached; windows that are cached are skipped:

    ./.venv/bin/python scripts/build_history_data.py --fetch-wikidata --contact you@example.com \\
        --wikidata-kinds film,game,birth,tv,console,space,company,invention \\
        --film-since 1981 --game-since 1981 --tv-since 1960 \\
        --birth-since 1941 --birth-occupations actor --min-sitelinks-birth 40

Wikidata responses are cached under scripts/history_sources/wikidata_cache/ so
a re-run only fetches windows that are missing. Every later build (with or
without --fetch-wikidata) merges whatever is in that cache.

The Wikidata part has NOT been run against the live service yet. Expect to tune
--min-sitelinks-* and the year window size if queries time out or return too
many/few rows.
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.history_rules import GRIM_PATTERN, classify  # noqa: E402

CLASSIC_PATH = ROOT / "app" / "data" / "history.json"
CURATED_PATH = ROOT / "scripts" / "history_sources" / "curated_milestones.json"
CACHE_DIR = ROOT / "scripts" / "history_sources" / "wikidata_cache"
OUTPUT_PATH = ROOT / "app" / "data" / "history_themed.json"
BIRTHDAYS_PATH = ROOT / "app" / "data" / "birthdays.json"

# Categories the module understands (keep in sync with app/modules/history.py).
CATEGORIES = {"film_tv", "music", "games", "tech", "culture", "science"}

PER_DAY_CAP = 14
BIRTHDAYS_PER_DAY_CAP = 15  # people kept per calendar day in birthdays.json
MAX_TEXT_LEN = 170

WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Wikidata identifiers used by the queries below. --check-ids fetches each
# one's English label and compares it with the expected label so a wrong ID is
# caught before any real query runs.
WD_IDS: Dict[str, str] = {
    "Q5": "human",
    "Q11424": "film",
    "Q7889": "video game",
    "Q33999": "actor",
    "Q177220": "singer",
    "Q639669": "musician",
    "Q2526255": "film director",
    "Q245068": "comedian",
    "Q2252262": "rapper",
    "Q2405480": "voice actor",
    "Q82594": "computer scientist",
    "Q5482740": "programmer",
    "Q5398426": "television series",
    "Q8076": "video game console",
    "Q4830453": "business",
    "Q6881511": "enterprise",
    "Q783794": "company",
    "Q891723": "public company",
    "P31": "instance of",
    "P279": "subclass of",
    "P106": "occupation",
    "P569": "date of birth",
    "P577": "publication date",
    "P580": "start time",
    "P571": "inception",
    "P619": "UTC date of spacecraft launch",
    "P575": "time of discovery or invention",
    "P61": "discoverer or inventor",
}

# Occupation priority for birthdays (first match wins when someone has several).
BIRTH_OCCUPATIONS: List[Tuple[str, str]] = [
    ("Q33999", "actor"),
    ("Q2405480", "voice actor"),
    ("Q177220", "singer"),
    ("Q2252262", "rapper"),
    ("Q639669", "musician"),
    ("Q2526255", "film director"),
    ("Q245068", "comedian"),
    ("Q82594", "computer scientist"),
    ("Q5482740", "programmer"),
]

Entry = Dict[str, Any]  # keys: m, d, y, c, s, t, src
HttpGet = Callable[..., Any]

# Wikidata kinds the generator can fetch. WIKIDATA_KINDS is what runs when
# --wikidata-kinds is not given; EXTRA_KINDS are opt-in so existing commands keep
# fetching and merging exactly what they did before.
WIKIDATA_KINDS = ("film", "game", "birth")
EXTRA_KINDS = ("tv", "console", "space", "company", "invention")
ALL_KINDS = WIKIDATA_KINDS + EXTRA_KINDS

# Earliest year fetched per kind unless a --<kind>-since flag says otherwise.
DEFAULT_SINCE: Dict[str, int] = {
    "film": 1920, "game": 1970, "birth": 1850,
    "tv": 1950, "console": 1970, "space": 1957, "company": 1850, "invention": 1700,
}
# Minimum number of Wikipedia language editions (the fame cut-off) per kind.
DEFAULT_MIN_SITELINKS: Dict[str, int] = {
    "film": 45, "game": 30, "birth": 60,
    "tv": 40, "console": 25, "space": 45, "company": 60, "invention": 85,
}
# Launches this recent are skipped: the date may still be the planned one.
RECENT_LAUNCH_DAYS = 365

# Sparse kinds use wider year windows (multiples of --window-years) so they need
# far fewer queries.
WINDOW_MULTIPLIER: Dict[str, int] = {"console": 10, "space": 2, "company": 4, "invention": 8}


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

_STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "into", "their", "which",
    "first", "also", "after", "over", "about", "for", "its", "his", "her",
}


def shorten(text: str, limit: int = MAX_TEXT_LEN) -> str:
    """Trim long sentences for a narrow receipt, preferring a sentence boundary."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("; "))
    if boundary >= 60:
        return cut[: boundary + 1].rstrip("; ")
    return cut[: cut.rfind(" ")].rstrip(",;:") + "..."


def _words(text: str) -> set:
    return {
        w
        for w in re.findall(r"[a-z0-9][a-z0-9'-]+", text.lower())
        if len(w) >= 4 and w not in _STOPWORDS
    }


def is_duplicate(a: Entry, b: Entry) -> bool:
    """Same year and mostly the same words => same event."""
    if a["y"] != b["y"]:
        return False
    wa, wb = _words(a["t"]), _words(b["t"])
    if not wa or not wb:
        return a["t"].lower() == b["t"].lower()
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.35


# --------------------------------------------------------------------------
# Source 1: classic history.json
# --------------------------------------------------------------------------

_YEAR_PREFIX = re.compile(r"^(-?\d{1,4}):\s*(.*)$")


def load_classic(path: Path = CLASSIC_PATH) -> List[Entry]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries: List[Entry] = []
    for month, days in data.items():
        for day, events in days.items():
            for raw in events:
                match = _YEAR_PREFIX.match(raw)
                year, text = (int(match.group(1)), match.group(2)) if match else (0, raw)
                tagged = classify(text, year)
                if not tagged:
                    continue
                category, score = tagged
                entries.append(
                    {"m": int(month), "d": int(day), "y": year, "c": category,
                     "s": score, "t": shorten(text), "src": "classic"}
                )
    return entries


# --------------------------------------------------------------------------
# Source 2: curated milestones
# --------------------------------------------------------------------------

def load_curated(path: Path = CURATED_PATH) -> List[Entry]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    entries: List[Entry] = []
    for item in raw.get("entries", []):
        if item["c"] not in CATEGORIES:
            raise ValueError(f"Unknown category {item['c']!r} in curated entry {item}")
        date(2024, item["m"], item["d"])  # raises on impossible dates
        entries.append(
            {"m": item["m"], "d": item["d"], "y": item["y"], "c": item["c"],
             "s": item["s"], "t": item["t"], "src": "curated"}
        )
    return entries


# --------------------------------------------------------------------------
# Source 3: Wikidata (optional)
# --------------------------------------------------------------------------

def _window_filter(start_year: int, end_year: int) -> str:
    return (
        f'FILTER(?date >= "{start_year}-01-01T00:00:00Z"^^xsd:dateTime && '
        f'?date < "{end_year + 1}-01-01T00:00:00Z"^^xsd:dateTime)'
    )


def build_release_query(class_qid: str, min_sitelinks: int, start_year: int, end_year: int) -> str:
    """Films or video games with a day-precision publication date in the window."""
    return f"""
SELECT ?item ?itemLabel ?date ?sl WHERE {{
  ?item wdt:P31 wd:{class_qid} ;
        wikibase:sitelinks ?sl ;
        p:P577/psv:P577 ?v .
  ?v wikibase:timeValue ?date ;
     wikibase:timePrecision ?prec .
  FILTER(?prec >= 11)
  FILTER(?sl >= {min_sitelinks})
  {_window_filter(start_year, end_year)}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
""".strip()


def build_birth_query(
    min_sitelinks: int,
    start_year: int,
    end_year: int,
    occupations: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """People in the listed occupations born in the window (day-precision dates)."""
    values = " ".join(f"wd:{qid}" for qid, _ in (occupations or BIRTH_OCCUPATIONS))
    return f"""
SELECT ?item ?itemLabel ?date ?sl ?occ WHERE {{
  VALUES ?occ {{ {values} }}
  ?item wdt:P31 wd:Q5 ;
        wdt:P106 ?occ ;
        wikibase:sitelinks ?sl ;
        p:P569/psv:P569 ?v .
  ?v wikibase:timeValue ?date ;
     wikibase:timePrecision ?prec .
  FILTER(?prec >= 11)
  FILTER(?sl >= {min_sitelinks})
  {_window_filter(start_year, end_year)}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
""".strip()


def _date_query(match: str, date_clause: str, min_sitelinks: int, start_year: int,
                end_year: int, description: bool = False) -> str:
    """Shared shape of the newer kinds: items matching `match` with a day-precision date.

    `date_clause` must bind ?v to the date value node. The description is only
    selected when the entry text or the positivity checks need it.
    """
    desc = " ?itemDescription" if description else ""
    return f"""
SELECT ?item ?itemLabel{desc} ?date ?sl WHERE {{
  {match}
  ?item wikibase:sitelinks ?sl .
  FILTER(?sl >= {min_sitelinks})
  {date_clause}
  ?v wikibase:timeValue ?date ;
     wikibase:timePrecision ?prec .
  FILTER(?prec >= 11)
  {_window_filter(start_year, end_year)}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
""".strip()


def build_tv_query(min_sitelinks: int, start_year: int, end_year: int) -> str:
    """TV series by first air date (publication date, or start time when that is what is set)."""
    return _date_query(
        "?item wdt:P31 wd:Q5398426 .",
        "{ ?item p:P577/psv:P577 ?v . } UNION { ?item p:P580/psv:P580 ?v . }",
        min_sitelinks, start_year, end_year,
    )


def build_console_query(min_sitelinks: int, start_year: int, end_year: int) -> str:
    """Game consoles (including handhelds and other subclasses) by release date."""
    return _date_query(
        "?item wdt:P31/wdt:P279* wd:Q8076 .",
        "?item p:P577/psv:P577 ?v .",
        min_sitelinks, start_year, end_year,
    )


def build_space_query(min_sitelinks: int, start_year: int, end_year: int) -> str:
    """Spacecraft and missions by launch date."""
    return _date_query(
        "",
        "?item p:P619/psv:P619 ?v .",
        min_sitelinks, start_year, end_year, description=True,
    )


def build_company_query(min_sitelinks: int, start_year: int, end_year: int) -> str:
    """Businesses by founding date (the description decides if they are tech/media/games)."""
    return _date_query(
        "VALUES ?cls { wd:Q4830453 wd:Q6881511 wd:Q783794 wd:Q891723 }\n"
        "  ?item wdt:P31 ?cls .",
        "?item p:P571/psv:P571 ?v .",
        min_sitelinks, start_year, end_year, description=True,
    )


def build_invention_query(min_sitelinks: int, start_year: int, end_year: int) -> str:
    """Things with a named inventor or discoverer, by discovery or invention date."""
    return _date_query(
        "FILTER EXISTS { ?item wdt:P61 ?who . }",
        "?item p:P575/psv:P575 ?v .",
        min_sitelinks, start_year, end_year, description=True,
    )


def year_windows(start: int, end: int, size: int) -> List[Tuple[int, int]]:
    windows = []
    year = start
    while year <= end:
        windows.append((year, min(year + size - 1, end)))
        year += size
    return windows


def _user_agent(contact: str) -> str:
    return f"paper-console-history-builder/1.0 ({contact})"


def run_sparql(
    query: str,
    contact: str,
    http_get: Optional[HttpGet] = None,
    retries: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> List[Dict[str, Any]]:
    """Run one SPARQL query, retrying on 429/5xx/timeouts. Returns result bindings."""
    if http_get is None:
        import requests

        http_get = requests.get

    headers = {"User-Agent": _user_agent(contact), "Accept": "application/sparql-results+json"}
    last_error: Optional[str] = None
    for attempt in range(retries):
        try:
            response = http_get(
                WDQS_ENDPOINT, params={"query": query, "format": "json"},
                headers=headers, timeout=90,
            )
        except Exception as exc:  # network error / timeout
            last_error = str(exc)
            sleep(5 * (attempt + 1))
            continue

        status = getattr(response, "status_code", 200)
        if status == 429 or status >= 500:
            retry_after = 0
            try:
                retry_after = int(response.headers.get("Retry-After", 0))
            except (TypeError, ValueError, AttributeError):
                pass
            last_error = f"HTTP {status}"
            sleep(max(retry_after, 10 * (attempt + 1)))
            continue
        if status >= 400:
            raise RuntimeError(f"Wikidata query failed with HTTP {status}: {response.text[:300]}")
        return response.json()["results"]["bindings"]

    raise RuntimeError(f"Wikidata query failed after {retries} attempts ({last_error})")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def bindings_to_rows(kind: str, bindings: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten SPARQL bindings into small cache rows."""
    rows = []
    for b in bindings:
        label = b.get("itemLabel", {}).get("value", "")
        row = {
            "qid": _qid(b["item"]["value"]),
            "label": label,
            "date": b["date"]["value"][:10],
            "sl": int(b["sl"]["value"]),
            "occ": _qid(b["occ"]["value"]) if "occ" in b else None,
        }
        description = b.get("itemDescription", {}).get("value", "")
        if description:  # only the kinds that select it (space, company, invention)
            row["desc"] = description
        rows.append(row)
    return rows


def _occupation_tag(occupations: Optional[List[Tuple[str, str]]]) -> str:
    """Cache-name tag for a restricted occupation list ('' for the full default list).

    Keeps a window fetched for actors only from being mistaken for a window that
    covers every occupation if the filter is widened later.
    """
    if not occupations or list(occupations) == BIRTH_OCCUPATIONS:
        return ""
    return "-".join(name.replace(" ", "-") for _, name in occupations)


def _cache_path(cache_dir: Path, kind: str, start: int, end: int, tag: str = "") -> Path:
    # The kind is always the part before the first underscore (load_wikidata_cache relies on it).
    middle = f"_{tag}" if tag else ""
    return cache_dir / f"{kind}{middle}_{start}_{end}.json"


def _kind_query(kind: str, min_sitelinks: int, start: int, end: int,
                occupations: Optional[List[Tuple[str, str]]] = None) -> str:
    if kind == "film":
        return build_release_query("Q11424", min_sitelinks, start, end)
    if kind == "game":
        return build_release_query("Q7889", min_sitelinks, start, end)
    if kind == "birth":
        return build_birth_query(min_sitelinks, start, end, occupations)
    builders = {
        "tv": build_tv_query, "console": build_console_query, "space": build_space_query,
        "company": build_company_query, "invention": build_invention_query,
    }
    return builders[kind](min_sitelinks, start, end)


def fetch_wikidata(
    cache_dir: Path,
    contact: str,
    min_sitelinks: Dict[str, int],
    window_size: int = 5,
    delay: float = 2.0,
    refresh: bool = False,
    http_get: Optional[HttpGet] = None,
    sleep: Callable[[float], None] = time.sleep,
    this_year: Optional[int] = None,
    kinds: Optional[Iterable[str]] = None,
    since: Optional[Dict[str, int]] = None,
    occupations: Optional[List[Tuple[str, str]]] = None,
) -> int:
    """Fetch any missing year windows into cache_dir. Returns number fetched.

    kinds limits what is fetched (default: film, game, birth; the newer kinds in
    EXTRA_KINDS are opt-in), since gives the earliest year per kind (see
    DEFAULT_SINCE) and occupations narrows the birthday query (default: every
    entry in BIRTH_OCCUPATIONS). min_sitelinks may omit kinds; DEFAULT_MIN_SITELINKS
    fills the gaps.
    """
    this_year = this_year or datetime.now().year
    cache_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(kinds) if kinds is not None else set(WIKIDATA_KINDS)
    first_year = {**DEFAULT_SINCE, **(since or {})}
    mins = {**DEFAULT_MIN_SITELINKS, **min_sitelinks}
    birth_tag = _occupation_tag(occupations)

    jobs = []
    for kind in ALL_KINDS:  # fixed order: film, game, birth, then the newer kinds
        if kind not in wanted:
            continue
        last_year = this_year - 15 if kind == "birth" else this_year
        size = window_size * WINDOW_MULTIPLIER.get(kind, 1)
        for start, end in year_windows(first_year[kind], last_year, size):
            query = _kind_query(kind, mins[kind], start, end, occupations)
            jobs.append((kind, start, end, birth_tag if kind == "birth" else "", query))

    fetched = 0
    for kind, start, end, tag, query in jobs:
        path = _cache_path(cache_dir, kind, start, end, tag)
        if path.exists() and not refresh:
            continue
        print(f"  fetching {kind} {start}-{end} ...", flush=True)
        rows = bindings_to_rows(kind, run_sparql(query, contact, http_get=http_get, sleep=sleep))
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"    {len(rows)} rows")
        fetched += 1
        sleep(delay)
    return fetched


def check_wikidata_ids(contact: str, http_get: Optional[HttpGet] = None) -> List[str]:
    """Return a list of problems (empty when every ID has the expected label)."""
    if http_get is None:
        import requests

        http_get = requests.get

    problems: List[str] = []
    for kind, ids in (("entities", [i for i in WD_IDS if i.startswith("Q")]),
                      ("properties", [i for i in WD_IDS if i.startswith("P")])):
        response = http_get(
            WIKIDATA_API,
            params={"action": "wbgetentities", "ids": "|".join(ids), "props": "labels",
                    "languages": "en", "format": "json"},
            headers={"User-Agent": _user_agent(contact)},
            timeout=30,
        )
        entities = response.json().get("entities", {})
        for wd_id in ids:
            label = entities.get(wd_id, {}).get("labels", {}).get("en", {}).get("value")
            expected = WD_IDS[wd_id]
            if label is None:
                problems.append(f"{wd_id}: no English label returned (expected {expected!r})")
            elif label.lower() != expected.lower():
                problems.append(f"{wd_id}: label is {label!r}, expected {expected!r}")
    return problems


def _best_cache_rows(
    cache_dir: Path,
    kinds: Iterable[str],
    since: Optional[Dict[str, int]] = None,
    occupations: Optional[List[Tuple[str, str]]] = None,
    min_sitelinks: Optional[Dict[str, int]] = None,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """One cached row per (kind, Wikidata item), honouring the scope limits.

    kinds, since, occupations and min_sitelinks apply the same limits as
    fetch_wikidata, so windows cached by an earlier, wider or less selective run
    never leak into a narrower or stricter build. (Raising a fame cut-off needs no
    new fetch; lowering one below what was fetched does.)
    A film or game keeps its earliest release date; a person keeps the
    highest-priority occupation (see BIRTH_OCCUPATIONS).
    """
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not cache_dir.exists():
        return best

    wanted = set(kinds)
    first_year = {**DEFAULT_SINCE, **(since or {})}
    allowed_occupations = {qid for qid, _ in occupations} if occupations else None
    occupation_rank = {qid: i for i, (qid, _) in enumerate(BIRTH_OCCUPATIONS)}

    for path in sorted(cache_dir.glob("*.json")):
        kind = path.name.split("_", 1)[0]
        if kind not in wanted:
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            if int(str(row["date"])[:4]) < first_year[kind]:
                continue  # before the requested start year
            if min_sitelinks and row["sl"] < min_sitelinks.get(kind, 0):
                continue  # not famous enough for the current cut-off
            if kind == "birth" and allowed_occupations is not None \
                    and row["occ"] not in allowed_occupations:
                continue
            key = (kind, row["qid"])
            current = best.get(key)
            if current is None:
                best[key] = dict(row)
                continue
            if kind == "birth":
                # keep the highest-priority occupation; dates for one person should agree
                if occupation_rank.get(row["occ"], 99) < occupation_rank.get(current["occ"], 99):
                    current["occ"] = row["occ"]
            elif row["date"] < current["date"]:
                current["date"] = row["date"]  # earliest release wins
    return best


def _usable_label(row: Dict[str, Any]) -> bool:
    label = row["label"]
    return bool(label) and not re.fullmatch(r"Q\d+", label)  # unlabelled items show as their QID


# Missions that ended in tragedy: their launch date must never print as good news.
_SPACE_DENY = re.compile(
    r"\b(challenger|columbia|sts-51-l|sts-107|soyuz 1|soyuz 11|soyuz t-10|soyuz ms-10|"
    r"apollo 1|mars climate orbiter|mars polar lander|phobos-grunt|spaceshiptwo)\b",
    re.IGNORECASE,
)

# Inventions/discoveries that are medical or harmful, on top of GRIM_PATTERN.
_SCIENCE_DENY = re.compile(
    r"\b(diseases?|virus(?:es)?|syndrome|cancer|infect\w*|toxi\w*|poison\w*|drugs?|opioid\w*|"
    r"narcotic\w*|tobacco|cigarettes?|pathogen\w*|parasit\w*|bacteri\w*|disorder|illness|"
    r"epidemic|explosive\w*|dynamite|gunpowder|firearms?)\b",
    re.IGNORECASE,
)

# Wikidata sometimes carries a "launch date" for things that are not spacecraft
# (a TV network, a mobile game); those are not space entries.
_NOT_SPACE = re.compile(
    r"\b(television|network|games?|apps?|video|films?|company|software|mobile|streaming|series|channel)\b",
    re.IGNORECASE,
)

# Discoveries and inventions share one Wikidata property, so the verb has to be
# guessed. Human-made things (devices, software, methods) are "invented"; everything
# else - planets, elements, places, finds - is "discovered", which is right far more
# often than the other way round. (Generic words like "system" or "language" are left
# out on purpose: "planet in the Solar System" and "inscribed in three languages"
# describe discoveries.)
_INVENTION_WORDS = re.compile(
    r"\b(devices?|machines?|apparatus|software|computers?|programs?|programming|"
    r"browsers?|protocols?|techniques?|methods?|algorithms?|engines?|vehicles?|instruments?|"
    r"tools?|cameras?|process|technolog\w*|media|images?|animation|games?|toys?)\b",
    re.IGNORECASE,
)

# A company only counts when its description says it is about one of these.
_COMPANY_ALLOW = re.compile(
    r"\b(technolog\w*|software|internet|online|computers?|electronics?|semiconductors?|hardware|"
    r"video games?|gaming|search engine|social (?:network|media)|e-commerce|streaming|animation|"
    r"film|motion pictures?|studios?|entertainment|media|broadcast\w*|television|toys?|comics?|"
    r"theme parks?|amusement|telecommunications?|mobile)\b",
    re.IGNORECASE,
)
_COMPANY_CATEGORY_RULES = [
    ("games", re.compile(r"\b(video games?|gaming)\b", re.IGNORECASE)),
    ("culture", re.compile(r"\b(toys?|comics?|theme parks?|amusement)\b", re.IGNORECASE)),
    ("film_tv", re.compile(
        r"\b(animation|film|motion pictures?|studios?|entertainment|media|broadcast\w*|"
        r"television|streaming)\b", re.IGNORECASE)),
]

# Kinds that become On This Day entries: kind -> (category, entry text builder).
# A category of None means "decide from the row" (see _entry_category).
_SIMPLE_ENTRY_KINDS = {
    "film": ("film_tv", "{label} is released."),
    "game": ("games", "{label} is released."),
    "tv": ("film_tv", "{label} premieres."),
    "console": ("games", "{label} is released."),
    "space": ("science", "{label} launches."),
}
ENTRY_KINDS = frozenset(_SIMPLE_ENTRY_KINDS) | {"company", "invention"}


def company_category(description: str) -> Optional[str]:
    """film_tv/games/culture/tech for a tech, media or games company; None for any other business."""
    if not description or GRIM_PATTERN.search(description) or not _COMPANY_ALLOW.search(description):
        return None
    for category, pattern in _COMPANY_CATEGORY_RULES:
        if pattern.search(description):
            return category
    return "tech"


def _entry_text_and_category(kind: str, row: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(text, category) for a cached row, or None when it should not be printed."""
    label = row["label"]
    description = row.get("desc", "")
    if kind in _SIMPLE_ENTRY_KINDS:
        category, template = _SIMPLE_ENTRY_KINDS[kind]
        if kind == "space" and (
            _SPACE_DENY.search(label) or GRIM_PATTERN.search(description)
            or _NOT_SPACE.search(description)
        ):
            return None
        return template.format(label=label), category
    if kind == "company":
        category = company_category(description)
        if category is None or GRIM_PATTERN.search(label):
            return None
        return f"{label} is founded.", category
    if kind == "invention":
        label = re.sub(r"^\d+\s+(?=[A-Z])", "", label)  # "2 Pallas" -> "Pallas"
        combined = f"{label} {description}"
        if GRIM_PATTERN.search(combined) or _SCIENCE_DENY.search(combined):
            return None
        verb = "invented" if _INVENTION_WORDS.search(combined) else "discovered"
        return f"{label} is {verb}.", "science"
    return None


def load_wikidata_cache(
    cache_dir: Path = CACHE_DIR,
    kinds: Optional[Iterable[str]] = None,
    since: Optional[Dict[str, int]] = None,
    occupations: Optional[List[Tuple[str, str]]] = None,
    today: Optional[date] = None,
    min_sitelinks: Optional[Dict[str, int]] = None,
) -> List[Entry]:
    """Cached films, games, TV, consoles, launches, companies and inventions as entries.

    Births are not returned here; they go to their own file (load_birth_rows).
    Rows dated after `today` (announced but not yet released) are skipped.
    """
    wanted = (set(kinds) if kinds is not None else set(WIKIDATA_KINDS)) & ENTRY_KINDS
    best = _best_cache_rows(cache_dir, wanted, since, occupations, min_sitelinks)
    today = today or date.today()

    entries: List[Entry] = []
    for (kind, _qid_value), row in best.items():
        if not _usable_label(row):
            continue
        try:
            when = date.fromisoformat(row["date"])
        except ValueError:
            continue
        if when > today:
            continue
        if kind == "space" and when > today - timedelta(days=RECENT_LAUNCH_DAYS):
            continue  # Wikidata often still holds a launch's planned date for a while
        built = _entry_text_and_category(kind, row)
        if built is None:
            continue
        text, category = built
        entries.append(
            {"m": when.month, "d": when.day, "y": when.year, "c": category,
             "s": min(88, round(25 + row["sl"] * 0.35)),
             "t": text, "src": "wikidata"}
        )
    return entries


def load_birth_rows(
    cache_dir: Path = CACHE_DIR,
    since: Optional[Dict[str, int]] = None,
    occupations: Optional[List[Tuple[str, str]]] = None,
    min_sitelinks: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Cached people as {m, d, y, n (name), o (occupation), s (Wikipedia language editions)}."""
    occupation_name = dict(BIRTH_OCCUPATIONS)
    rows: List[Dict[str, Any]] = []
    for (_kind, _qid_value), row in _best_cache_rows(
            cache_dir, {"birth"}, since, occupations, min_sitelinks).items():
        if not _usable_label(row):
            continue
        try:
            when = date.fromisoformat(row["date"])
        except ValueError:
            continue
        rows.append({"m": when.month, "d": when.day, "y": when.year, "n": row["label"],
                     "o": occupation_name.get(row["occ"], ""), "s": row["sl"]})
    return rows

# --------------------------------------------------------------------------
# Merge + write
# --------------------------------------------------------------------------

_SOURCE_PRIORITY = {"curated": 0, "wikidata": 1, "classic": 2}


def merge(entries: Iterable[Entry], per_day_cap: int = PER_DAY_CAP) -> Dict[str, Dict[str, List[Entry]]]:
    by_day: Dict[Tuple[int, int], List[Entry]] = defaultdict(list)
    for e in entries:
        by_day[(e["m"], e["d"])].append(e)

    merged: Dict[str, Dict[str, List[Entry]]] = {}
    for (m, d), day_entries in sorted(by_day.items()):
        day_entries.sort(key=lambda e: (-e["s"], _SOURCE_PRIORITY.get(e["src"], 9), -e["y"]))
        kept: List[Entry] = []
        for e in day_entries:
            if len(kept) >= per_day_cap:
                break
            if any(is_duplicate(e, k) for k in kept):
                continue
            kept.append(e)
        merged.setdefault(str(m), {})[str(d)] = kept
    return merged


def write_themed(path: Path, merged: Dict[str, Dict[str, List[Entry]]], sources: List[str]) -> None:
    """One day per line so the file diffs cleanly."""
    meta = {"version": 1, "generated": date.today().isoformat(), "sources": sources}
    lines = ["{", f'"meta": {json.dumps(meta)},', '"events": {']
    month_blocks = []
    for month in sorted(merged, key=int):
        day_lines = []
        for day in sorted(merged[month], key=int):
            compact = [
                {"y": e["y"], "t": e["t"], "c": e["c"], "s": e["s"]} for e in merged[month][day]
            ]
            day_lines.append(f'  "{day}": {json.dumps(compact, ensure_ascii=False, separators=(",", ":"))}')
        month_blocks.append(f' "{month}": {{\n' + ",\n".join(day_lines) + "\n }")
    lines.append(",\n".join(month_blocks))
    lines.extend(["}", "}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def build_birthdays(rows: Iterable[Dict[str, Any]], cap: int = BIRTHDAYS_PER_DAY_CAP
                    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Group people by calendar day, most widely covered first, at most `cap` per day."""
    by_day: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[(row["m"], row["d"])].append(row)

    result: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for (m, d), people in sorted(by_day.items()):
        people.sort(key=lambda r: (-r["s"], -r["y"], r["n"]))
        result.setdefault(str(m), {})[str(d)] = [
            {"y": r["y"], "n": r["n"], "o": r["o"], "s": r["s"]} for r in people[:cap]
        ]
    return result


def write_birthdays(path: Path, births: Dict[str, Dict[str, List[Dict[str, Any]]]],
                    sources: List[str]) -> None:
    """One day per line so the file diffs cleanly."""
    meta = {"version": 1, "generated": date.today().isoformat(), "sources": sources}
    lines = ["{", f'"meta": {json.dumps(meta)},', '"births": {']
    month_blocks = []
    for month in sorted(births, key=int):
        day_lines = []
        for day in sorted(births[month], key=int):
            day_lines.append(
                f'  "{day}": {json.dumps(births[month][day], ensure_ascii=False, separators=(",", ":"))}'
            )
        month_blocks.append(f' "{month}": {{\n' + ",\n".join(day_lines) + "\n }")
    lines.append(",\n".join(month_blocks))
    lines.extend(["}", "}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def birthdays_summary(births: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> str:
    covered = sum(1 for days in births.values() for people in days.values() if people)
    total = sum(len(people) for days in births.values() for people in days.values())
    return f"{total} people across {covered} of 366 calendar days"


def coverage_summary(merged: Dict[str, Dict[str, List[Entry]]]) -> str:
    covered = sum(1 for days in merged.values() for entries in days.values() if entries)
    total = sum(len(e) for days in merged.values() for e in days.values())
    return f"{total} entries across {covered} of 366 calendar days"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output", type=Path, default=OUTPUT_PATH)
    p.add_argument("--birthdays-output", type=Path, default=BIRTHDAYS_PATH,
                   help="Where the Birthdays module's data is written (default: %(default)s)")
    p.add_argument("--births-per-day", type=int, default=BIRTHDAYS_PER_DAY_CAP,
                   help="Most people kept per calendar day (default: %(default)s)")
    p.add_argument("--classic", type=Path, default=CLASSIC_PATH)
    p.add_argument("--curated", type=Path, default=CURATED_PATH)
    p.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    p.add_argument("--no-classic", action="store_true", help="Skip tagging entries from history.json")
    p.add_argument("--check-ids", action="store_true",
                   help="Verify the Wikidata IDs used by the queries, then exit (needs internet)")
    p.add_argument("--fetch-wikidata", action="store_true",
                   help="Fetch missing Wikidata windows into the cache before merging (needs internet)")
    p.add_argument("--refresh", action="store_true", help="Re-fetch windows that are already cached")
    p.add_argument("--contact", default="", help="Email or URL put in the User-Agent (required for Wikidata)")
    p.add_argument("--window-years", type=int, default=5)
    p.add_argument("--delay", type=float, default=2.0, help="Seconds to wait between queries")
    p.add_argument("--min-sitelinks-film", type=int, default=45)
    p.add_argument("--min-sitelinks-game", type=int, default=30)
    p.add_argument("--min-sitelinks-birth", type=int, default=60)
    p.add_argument("--wikidata-kinds", default=",".join(WIKIDATA_KINDS), metavar="LIST",
                   help="Comma-separated Wikidata kinds to fetch and merge: "
                        + ", ".join(ALL_KINDS) + " (default: " + ", ".join(WIKIDATA_KINDS) + ")")
    p.add_argument("--film-since", type=int, default=DEFAULT_SINCE["film"], metavar="YEAR",
                   help="Earliest film release year (default: %(default)s)")
    p.add_argument("--game-since", type=int, default=DEFAULT_SINCE["game"], metavar="YEAR",
                   help="Earliest video game release year (default: %(default)s)")
    p.add_argument("--birth-since", type=int, default=DEFAULT_SINCE["birth"], metavar="YEAR",
                   help="Earliest birth year (default: %(default)s)")
    for kind in EXTRA_KINDS:
        p.add_argument(f"--{kind}-since", type=int, default=DEFAULT_SINCE[kind], metavar="YEAR",
                       help=f"Earliest {kind} year (default: %(default)s)")
        p.add_argument(f"--min-sitelinks-{kind}", type=int, default=DEFAULT_MIN_SITELINKS[kind],
                       help=f"Fame cut-off for {kind} (default: %(default)s)")
    p.add_argument("--birth-occupations", default="all", metavar="LIST",
                   help="Comma-separated occupations for birthdays, or 'all' (default). "
                        "Choices: " + ", ".join(name for _, name in BIRTH_OCCUPATIONS))
    args = p.parse_args(argv)

    kinds = [k.strip() for k in args.wikidata_kinds.split(",") if k.strip()]
    bad_kinds = [k for k in kinds if k not in ALL_KINDS]
    if bad_kinds or not kinds:
        p.error(f"--wikidata-kinds must be a comma-separated subset of {', '.join(ALL_KINDS)}")
    args.wikidata_kinds = kinds

    if args.birth_occupations.strip().lower() == "all":
        args.birth_occupations = None
    else:
        by_name = {name: (qid, name) for qid, name in BIRTH_OCCUPATIONS}
        names = [n.strip().lower() for n in args.birth_occupations.split(",") if n.strip()]
        bad_names = [n for n in names if n not in by_name]
        if bad_names or not names:
            p.error("--birth-occupations must be 'all' or a comma-separated list of: "
                    + ", ".join(by_name))
        # keep the priority order defined in BIRTH_OCCUPATIONS
        args.birth_occupations = [pair for pair in BIRTH_OCCUPATIONS if pair[1] in names]
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if (args.check_ids or args.fetch_wikidata) and not args.contact:
        print("--contact is required for Wikidata requests (Wikimedia asks for a contactable User-Agent).")
        return 2

    if args.check_ids:
        problems = check_wikidata_ids(args.contact)
        if problems:
            print("Wikidata ID check FAILED:")
            for line in problems:
                print("  -", line)
            return 1
        print(f"Wikidata ID check passed ({len(WD_IDS)} IDs).")
        return 0

    since = {"film": args.film_since, "game": args.game_since, "birth": args.birth_since}
    mins = {"film": args.min_sitelinks_film, "game": args.min_sitelinks_game,
            "birth": args.min_sitelinks_birth}
    for kind in EXTRA_KINDS:
        since[kind] = getattr(args, f"{kind}_since")
        mins[kind] = getattr(args, f"min_sitelinks_{kind}")

    if args.fetch_wikidata:
        count = fetch_wikidata(
            args.cache_dir, args.contact, mins,
            window_size=args.window_years, delay=args.delay, refresh=args.refresh,
            kinds=args.wikidata_kinds, since=since, occupations=args.birth_occupations,
        )
        print(f"Fetched {count} new Wikidata window(s).")

    entries: List[Entry] = []
    sources: List[str] = []
    if not args.no_classic:
        classic = load_classic(args.classic)
        entries += classic
        sources.append(f"classic history.json (keyword-tagged, {len(classic)} entries)")
    curated = load_curated(args.curated)
    entries += curated
    sources.append(f"curated milestones ({len(curated)} entries)")
    wikidata = load_wikidata_cache(
        args.cache_dir, kinds=args.wikidata_kinds, since=since, occupations=args.birth_occupations,
        min_sitelinks=mins,
    )
    if wikidata:
        entries += wikidata
        sources.append(f"Wikidata cache ({len(wikidata)} entries)")

    merged = merge(entries)
    write_themed(args.output, merged, sources)
    print(f"Wrote {args.output}: {coverage_summary(merged)}")
    for line in sources:
        print("  source:", line)

    # Famous birthdays live in their own file for the Birthdays module.
    if "birth" in args.wikidata_kinds:
        birth_rows = load_birth_rows(args.cache_dir, since=since, occupations=args.birth_occupations,
                                     min_sitelinks=mins)
        if birth_rows:
            births = build_birthdays(birth_rows, args.births_per_day)
            write_birthdays(args.birthdays_output, births,
                            [f"Wikidata cache ({len(birth_rows)} people)"])
            print(f"Wrote {args.birthdays_output}: {birthdays_summary(births)}")
        else:
            print(f"No birthdays in the Wikidata cache, so {args.birthdays_output} was left as it is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
