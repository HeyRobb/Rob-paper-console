"""Keyword rules shared by the History module and scripts/build_history_data.py.

Two jobs:
  * GRIM_PATTERN - spot classic history entries about wars, disasters, deaths
    and politics so the module can skip them when it needs a calmer fallback.
  * classify() - tag a classic "On This Day" sentence as film/TV, music, games,
    tech or culture so it can join the themed dataset.

These are deliberately simple heuristics. Curated and Wikidata-sourced entries
carry their category explicitly and never go through classify().
"""

import re
from typing import Optional, Tuple

GRIM_PATTERN = re.compile(
    r"\b(kill\w*|dead|deaths?|die[sd]?|massacre\w*|murder\w*|assassinat\w*|wars?|"
    r"battles?|sieges?|invasions?|invad\w*|bomb\w*|attacks?|attacked|hostages?|"
    r"hijack\w*|execut\w*|genocide|coups?|treat(?:y|ies)|elections?|elected|"
    r"crash\w*|earthquakes?|tsunamis?|fires?|floods?|disasters?|terroris\w*|"
    r"shootings?|shot|capitulat\w*|excommunicat\w*|independence|revolution\w*|"
    r"pope|king|emperor|prime minister|president|parliament|congress\w*|senate|"
    r"governor|minister|beat\w*|police|arrest\w*|protest\w*|riots?|trials?|"
    r"courts?|laws?|slavery|civil rights|strikes?|sanctions?|convict\w*|"
    r"sentenced|prison\w*|hanged|lynch\w*|persecut\w*|indict\w*|blacklist\w*|"
    r"scandals?|lawsuits?|sues?|suit|antitrust|Justice|Red Scare)\b",
    re.IGNORECASE,
)

# Things that match the pop-culture keywords below but are not what we want:
# classical music, opera, ballet, stage drama, archaeology, photographic film.
_NOT_POP_PATTERN = re.compile(
    r"\b(orchestra|symphon\w*|opera|ballet|philharmonic|choir|chorus|cantata|"
    r"oratorio|Mozart|Beethoven|Bach|Mahler|Brahms|Tchaikovsky|Verdi|Wagner|"
    r"archaeolog\w*|ancient|photograph\w*|celluloid)\b",
    re.IGNORECASE,
)

# Order matters: the first matching category wins.
CATEGORY_RULES = [
    (
        "games",
        re.compile(
            r"\b(video games?|arcade|Nintendo|Sega|Atari|PlayStation|Xbox|"
            r"Pok[eé]mon|Minecraft|Tetris|Pac-Man|Super Mario|Zelda|"
            r"Sonic the Hedgehog|esports?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "culture",
        re.compile(
            r"\b(comic strip|comic books?|comics|Superman|Batman|Spider-Man|"
            r"Mickey Mouse|Donald Duck|Barbie|LEGO|Monopoly board game|Peanuts|"
            r"Calvin and Hobbes|Harry Potter|Sherlock Holmes|theme park|"
            r"Disneyland|Disney World|Rubik's Cube|Hot Wheels|Slinky|Frisbee)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "film_tv",
        re.compile(
            r"\b(films?|movies?|motion pictures?|animated|cartoons?|sitcom|"
            r"television series|TV series|television show|game show|Hollywood|"
            r"box office|Academy Awards?|Oscars?|Emmys?|Golden Globes?|Pixar|"
            r"Star Wars|Star Trek|Doctor Who|"
            r"premieres? on (?:ABC|CBS|NBC|Fox|BBC|HBO|PBS|ITV|Netflix))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "music",
        re.compile(
            r"\b(albums?|songs?|rock band|pop group|boy band|concerts?|singers?|"
            r"Beatles|Rolling Stones|Grammys?|Grammy Awards?|rock and roll|"
            r"hip hop|rappers?|MTV|musicals?|record label|Billboard)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tech",
        re.compile(
            r"\b(computers?|Internet|software|microprocessors?|smartphones?|"
            r"iPhone|iPad|Apple Inc|Microsoft|Google|Facebook|Twitter|YouTube|"
            r"Amazon\.com|e-?mail|websites?|web browser|World Wide Web|ARPANET|"
            r"programming language|artificial intelligence|robots?|transistor|"
            r"integrated circuit|personal computer|operating system|search engine|"
            r"Wikipedia|mobile phone|cell(?:ular)? phone|text message|video call|"
            r"cryptocurrency|Bitcoin|ChatGPT|OpenAI|push-button telephone)\b",
            re.IGNORECASE,
        ),
    ),
]

_BONUS_PATTERN = re.compile(
    r"\b(first|debut|launch\w*|introduc\w*|releas\w*|premiere\w*)\b", re.IGNORECASE
)


def classify(text: str, year: int = 0) -> Optional[Tuple[str, int]]:
    """Return (category, score) for a classic history sentence, or None.

    Entries that read as war/disaster/politics (GRIM_PATTERN) or as classical
    arts/archaeology are never tagged. Scores land in the 50s-60s so curated
    entries (60-100) rank above them.
    """
    if GRIM_PATTERN.search(text) or _NOT_POP_PATTERN.search(text):
        return None

    for category, pattern in CATEGORY_RULES:
        if pattern.search(text):
            score = 50
            if _BONUS_PATTERN.search(text):
                score += 8
            if year >= 1950:
                score += 4
            return category, score
    return None
