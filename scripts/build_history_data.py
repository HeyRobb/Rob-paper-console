#!/usr/bin/env python3
"""Build app/data/history_themed.json for the History ("On This Day") module.

Sources, merged and de-duplicated per calendar day:
  1. app/data/history.json - classic entries, tagged by app.history_rules.classify()
  2. scripts/history_sources/curated_milestones.json - hand-checked milestones
  3. Wikidata (optional, needs internet) - films, video games and famous births,
     ranked by how many Wikipedia language editions cover them (a fame proxy).

Offline build (no network needed):

    ./.venv/bin/python scripts/build_history_data.py

Add Wikidata (run on a machine with normal internet access, not the sandbox):

    ./.venv/bin/python scripts/build_history_data.py --check-ids --contact you@example.com
    ./.venv/bin/python scripts/build_history_data.py --fetch-wikidata --contact you@example.com

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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.history_rules import classify  # noqa: E402

CLASSIC_PATH = ROOT / "app" / "data" / "history.json"
CURATED_PATH = ROOT / "scripts" / "history_sources" / "curated_milestones.json"
CACHE_DIR = ROOT / "scripts" / "history_sources" / "wikidata_cache"
OUTPUT_PATH = ROOT / "app" / "data" / "history_themed.json"

# Categories the module understands (keep in sync with app/modules/history.py).
CATEGORIES = {"film_tv", "music", "games", "tech", "culture", "birthday"}

PER_DAY_CAP = 14
BIRTHS_PER_DAY_CAP = 3
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
    "P31": "instance of",
    "P106": "occupation",
    "P569": "date of birth",
    "P577": "publication date",
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


def build_birth_query(min_sitelinks: int, start_year: int, end_year: int) -> str:
    """People in the listed occupations born in the window (day-precision dates)."""
    values = " ".join(f"wd:{qid}" for qid, _ in BIRTH_OCCUPATIONS)
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
        rows.append(
            {
                "qid": _qid(b["item"]["value"]),
                "label": label,
                "date": b["date"]["value"][:10],
                "sl": int(b["sl"]["value"]),
                "occ": _qid(b["occ"]["value"]) if "occ" in b else None,
            }
        )
    return rows


def _cache_path(cache_dir: Path, kind: str, start: int, end: int) -> Path:
    return cache_dir / f"{kind}_{start}_{end}.json"


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
) -> int:
    """Fetch any missing year windows into cache_dir. Returns number fetched."""
    this_year = this_year or datetime.now().year
    cache_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for start, end in year_windows(1920, this_year, window_size):
        jobs.append(("film", start, end, build_release_query("Q11424", min_sitelinks["film"], start, end)))
    for start, end in year_windows(1970, this_year, window_size):
        jobs.append(("game", start, end, build_release_query("Q7889", min_sitelinks["game"], start, end)))
    for start, end in year_windows(1850, this_year - 15, window_size):
        jobs.append(("birth", start, end, build_birth_query(min_sitelinks["birth"], start, end)))

    fetched = 0
    for kind, start, end, query in jobs:
        path = _cache_path(cache_dir, kind, start, end)
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


def load_wikidata_cache(cache_dir: Path = CACHE_DIR) -> List[Entry]:
    """Turn cached Wikidata rows into entries (earliest date per item, one occupation per person)."""
    if not cache_dir.exists():
        return []

    occupation_rank = {qid: i for i, (qid, _) in enumerate(BIRTH_OCCUPATIONS)}
    occupation_name = dict(BIRTH_OCCUPATIONS)

    # qid -> best row per kind
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for path in sorted(cache_dir.glob("*.json")):
        kind = path.name.split("_", 1)[0]
        if kind not in {"film", "game", "birth"}:
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
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

    entries: List[Entry] = []
    for (kind, _qid_value), row in best.items():
        label = row["label"]
        if not label or re.fullmatch(r"Q\d+", label):
            continue  # no English label
        try:
            when = date.fromisoformat(row["date"])
        except ValueError:
            continue
        sl = row["sl"]
        if kind == "birth":
            occupation = occupation_name.get(row["occ"], "")
            entries.append(
                {"m": when.month, "d": when.day, "y": when.year, "c": "birthday",
                 "s": min(78, round(20 + sl * 0.3)),
                 "t": f"Born: {label}, {occupation}." if occupation else f"Born: {label}.",
                 "src": "wikidata"}
            )
        else:
            entries.append(
                {"m": when.month, "d": when.day, "y": when.year,
                 "c": "film_tv" if kind == "film" else "games",
                 "s": min(88, round(25 + sl * 0.35)),
                 "t": f"{label} is released.", "src": "wikidata"}
            )
    return entries


# --------------------------------------------------------------------------
# Merge + write
# --------------------------------------------------------------------------

_SOURCE_PRIORITY = {"curated": 0, "wikidata": 1, "classic": 2}


def merge(entries: Iterable[Entry], per_day_cap: int = PER_DAY_CAP,
          births_cap: int = BIRTHS_PER_DAY_CAP) -> Dict[str, Dict[str, List[Entry]]]:
    by_day: Dict[Tuple[int, int], List[Entry]] = defaultdict(list)
    for e in entries:
        by_day[(e["m"], e["d"])].append(e)

    merged: Dict[str, Dict[str, List[Entry]]] = {}
    for (m, d), day_entries in sorted(by_day.items()):
        day_entries.sort(key=lambda e: (-e["s"], _SOURCE_PRIORITY.get(e["src"], 9), -e["y"]))
        kept: List[Entry] = []
        births = 0
        for e in day_entries:
            if len(kept) >= per_day_cap:
                break
            if e["c"] == "birthday" and births >= births_cap:
                continue
            if any(is_duplicate(e, k) for k in kept):
                continue
            kept.append(e)
            births += e["c"] == "birthday"
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
    return p.parse_args(argv)


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

    if args.fetch_wikidata:
        count = fetch_wikidata(
            args.cache_dir, args.contact,
            {"film": args.min_sitelinks_film, "game": args.min_sitelinks_game, "birth": args.min_sitelinks_birth},
            window_size=args.window_years, delay=args.delay, refresh=args.refresh,
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
    wikidata = load_wikidata_cache(args.cache_dir)
    if wikidata:
        entries += wikidata
        sources.append(f"Wikidata cache ({len(wikidata)} entries)")

    merged = merge(entries)
    write_themed(args.output, merged, sources)
    print(f"Wrote {args.output}: {coverage_summary(merged)}")
    for line in sources:
        print("  source:", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
