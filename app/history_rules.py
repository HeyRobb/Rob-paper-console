"""Keyword rules shared by the History module and scripts/build_history_data.py.

Two jobs:
  * GRIM_PATTERN - spot classic history entries that are not positive (deaths,
    violence, crime, wars, disasters, politics) so the module never prints them.
  * classify() - tag a classic "On This Day" sentence as film/TV, music, games,
    tech or culture so it can join the themed dataset.

These are deliberately simple heuristics. Curated and Wikidata-sourced entries
carry their category explicitly and never go through classify().
"""

import re
from typing import Optional, Tuple

# Anything that is not a positive, light "on this day" item: people dying or
# being killed, violence and crime, wars and the military, disasters and
# accidents, politics and government. The pattern is deliberately aggressive -
# there are plenty of entries to spare, and a wrongly dropped one costs far less
# than a grim one on a receipt. Watch the stems: "explo" would also match
# "exploration", "victor" would match "Victoria" and "beat" would match "Beatles".
_GRIM_TERMS = [
    # death and violence
    r"kill\w*", r"dead", r"deaths?", r"die[sd]?", r"dying", r"fatal\w*", r"perish\w*",
    r"slain", r"slay\w*", r"slaughter\w*", r"murder\w*", r"assassinat\w*", r"massacre\w*",
    r"genocide", r"holocaust", r"atrocit\w*", r"execut\w*", r"hanged", r"lynch\w*",
    r"tortur\w*", r"suicid\w*", r"stabb\w*", r"shootings?", r"shot", r"gunm[ae]n",
    r"victims?", r"wound\w*", r"injur\w*", r"casualt\w*", r"mass grave",
    r"concentration camps?", r"death camps?", r"the bod(?:y|ies) of",
    r"bod(?:y|ies) (?:is|was|are|were) (?:found|recovered|discovered)", r"corpses?", r"rape\w*", r"abus\w*", r"kidnap\w*",
    r"abduct\w*", r"hostages?", r"hijack\w*", r"terroris\w*", r"violen\w*", r"riots?",
    r"unrest", r"clash\w*", r"ambush\w*", r"fight\w*", r"combat", r"attack\w*",
    r"assault\w*", r"knock\w*", r"beat(?:s|en|ing)?", r"bomb\w*", r"explod\w*", r"explosi\w*",
    r"missiles?", r"weapons?", r"nuclear", r"torpedo\w*",
    # war and the military
    r"wars?", r"wartime", r"warfare", r"warlords?", r"warships?", r"warplanes?", r"battles?",
    r"sieges?", r"blockad\w*", r"invasions?", r"invad\w*", r"conquer\w*", r"conquest",
    r"occup\w*", r"annex\w*", r"surrender\w*", r"capitulat\w*", r"troops?", r"armies",
    r"army", r"armada", r"military", r"navy", r"naval", r"soldiers?", r"forces", r"offensive",
    r"raids?", r"raided", r"raiding", r"enem(?:y|ies)", r"defeat\w*", r"victory", r"victories", r"victorious",
    r"conflicts?", r"mutiny", r"rebel\w*", r"uprising", r"revolution\w*", r"coups?",
    r"overthr\w*", r"ousted", r"depose[sd]?", r"deposing", r"seiz\w*", r"ceasefire",
    r"armistice", r"truce", r"bombers?", r"fleet", r"submarines?", r"fascis\w*", r"nazis?",
    r"communis\w*", r"cold war", r"general secretary", r"espionage", r"treason",
    # disasters and accidents
    r"disasters?", r"earthquakes?", r"tsunamis?", r"fires?", r"wildfires?", r"floods?",
    r"hurricanes?", r"typhoons?", r"cyclones?", r"tornado\w*", r"avalanche\w*", r"landslides?",
    r"erupt\w*", r"volcan\w*", r"blizzards?", r"drought\w*", r"storms?", r"famine", r"plague",
    r"epidemic", r"pandemic", r"outbreaks?", r"crash\w*", r"collaps\w*", r"sink\w*", r"sank",
    r"sunk", r"drown\w*", r"wreck\w*", r"shipwreck\w*", r"derail\w*", r"collision",
    r"collid\w*", r"burn\w*", r"destroy\w*", r"crisis", r"incident", r"accident\w*",
    r"malfunction\w*", r"failures?", r"fail(?:s|ed|ing)?", r"starv\w*", r"grounded", r"grounds (?:the|its|all)",
    # crime, law and punishment
    r"arrest\w*", r"police", r"prison\w*", r"jail\w*", r"crimin\w*", r"robber\w*", r"(?<!grand )theft",
    r"stolen", r"steal\w*", r"fraud\w*", r"brib\w*", r"corrupt\w*", r"smuggl\w*",
    r"scandals?", r"lawsuits?", r"sues?", r"suit", r"antitrust", r"indict\w*", r"convict\w*",
    r"sentenced", r"trials?", r"courts?", r"laws?", r"justice", r"sanctions?", r"blacklist\w*",
    r"persecut\w*", r"slavery", r"strikes?", r"protest\w*", r"banned", r"deport\w*",
    r"expel\w*", r"exile\w*", r"excommunicat\w*", r"red scare",
    # politics and government
    r"resign\w*", r"impeach\w*", r"elections?", r"elected", r"vote[sd]?", r"voting",
    r"plebiscite", r"referendum", r"parliament", r"congress\w*", r"senate", r"governor",
    r"minister", r"prime minister", r"president", r"pope", r"king", r"queen",
    r"emperor", r"duke", r"throne", r"crown\w*", r"empire", r"regime", r"junta",
    r"dictator\w*", r"monarchy", r"republic", r"treat(?:y|ies)", r"agreements?", r"pacts?",
    r"alliance", r"negotiat\w*", r"diplomat\w*", r"embass(?:y|ies)",
    r"nationaliz\w*", r"independence", r"proclaim\w*", r"handed over", r"sovereign\w*",
    r"partition\w*", r"council", r"colon\w*", r"civil rights", r"hostile",
    r"independent", r"repeal\w*", r"citizens", r"citizenship", r"legislat\w*", r"immigra\w*",
    r"constitution\w*", r"polic(?:y|ies)", r"amendments?", r"appeal\w*", r"european union",
    r"european economic community", r"united nations", r"nato", r"attorney",
    r"autonomous (?:region|republic|province|area)", r"premier", r"chancellor", r"cabinet",
    r"parties", r"party", r"patriarch\w*", r"missionar\w*",
    r"liquidat\w*", r"insolven\w*", r"resistance", r"defensive", r"defen[cs]e", r"vikings?",
    # money trouble
    r"bankrupt\w*", r"recession", r"depression", r"inflation(?!\s+adjusted)",
]
GRIM_PATTERN = re.compile(r"\b(?:" + "|".join(_GRIM_TERMS) + r")\b", re.IGNORECASE)

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
