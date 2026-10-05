"""Understanding discovery requests ("find / where / recommend / open now...").

Turns a guest message into a structured DiscoveryQuery: categories, hard
constraints (diet, accessibility, pets, open-at, after-midnight, exclusions),
soft preferences (lively, quiet, local), time and proximity. Deterministic and
multilingual (en / cnr / ru). Returns None when the message is not a
discovery request - property questions ("What time is breakfast?") stay with
the property knowledge pack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.discovery.engine import DiscoveryQuery
from app.places.taxonomy import FAMILIES, SUBCATEGORIES, Category
from app.text import contains_phrase, fold
from app.transactions.slots import parse_when

ESSENTIAL = {Category.HEALTH.value, Category.FINANCIAL_SERVICE.value, Category.ESSENTIAL_SERVICE.value,
             Category.CONNECTIVITY.value, Category.MOBILITY_INFRASTRUCTURE.value}

# Explicit "look outside the property" cues.
CUES = [
    "find", "where can", "where is", "where are", "where do", "where to", "recommend*", "suggest*", "nearby",
    "near the hotel", "near here", "near me", "close by", "around here", "in town", "somewhere", "any good",
    "anything", "open now", "still open", "what's open", "whats open", "is open", "open late", "open after",
    "open today", "go for", "place to", "places to", "what can we", "what's happening", "whats happening",
    "happening tonight", "i need a", "i need an", "we need a", "looking for", "get tickets", "can we see",
    "gdje", "nadji", "pronadji", "preporu*", "u blizini", "u gradu", "negdje", "otvoren*", "sta je otvoreno",
    "treba mi", "trebamo", "trazim", "sta se desava", "sta ima",
    "где", "найди", "найдите", "посоветуй*", "порекоменд*", "рядом", "поблизости", "в городе", "куда",
    "открыт*", "нужна", "нужен", "нужно", "ищу", "что происходит", "что посмотреть",
]
# Generic needs -> whole categories / groups of subcategories.
GENERIC = [
    (["buy food", "groceries", "grocery", "where locals buy", "kupiti hranu", "namirnic*", "купить продукт*",
      "купить еду", "продукты"], set(), {"supermarket", "food_market"}),
    (["drinks", "drink", "go out", "night out", "nightlife", "izlazak", "izaci", "popiti", "pice",
      "выпить", "ночн* жизн*", "тусов*"], {"NIGHTLIFE"}, set()),
    (["dinner", "lunch", "eat", "food", "vecera", "veceru", "vecerati", "rucak", "rucat", "jesti", "hrana",
      "ужин*", "обед*", "поесть", "еда", "кухн*"], set(), {"restaurant", "konoba", "fast_food"}),
    (["see", "visit", "sightseeing", "attraction*", "what to do", "things to do", "posjet*", "vidjeti", "znamenit*",
      "посмотреть", "посетить", "достопримечательн*"], {"ATTRACTION", "CULTURE"}, set()),
]
EVENTS = ["event*", "happening", "concert*", "festival*", "show", "gig", "dogadjaj*", "koncert*", "festival*",
          "desava", "событи*", "концерт*", "фестивал*", "мероприяти*", "происходит"]
MUSIC = ["live music", "jazz", "svirka", "muzika uzivo", "dzez", "живая музыка", "живой музык*", "джаз"]
EXCLUDE_PREFIX = ["not a", "not an", "no", "without", "but not", "ne", "nije", "bez", "не", "без"]

REQUIRED = [
    (["vegetarian", "vegetarijansk*", "вегетариан*"], "vegetarian_options", True),
    (["vegan", "vegansk*", "веган*"], "vegan_options", True),
    (["gluten free", "gluten-free", "bez glutena", "без глютена"], "gluten_free_options", True),
    (["halal", "халяль", "халал"], "halal", True),
    (["kosher", "кошер*"], "kosher", True),
    (["wheelchair", "accessible", "invalidsk* kolic*", "pristupacn*", "инвалидн* коляск*", "доступн* для"],
     "wheelchair_access", True),
    (["with kids", "with children", "child friendly", "kid friendly", "family", "sa djecom", "za djecu",
      "с детьми", "для детей"], "child_friendly", True),
    (["dog", "pet", "pets", "pas", "psom", "ljubim*", "собак*", "питом*"], "pets_allowed", True),
    (["live music", "jazz", "svirka", "muzika uzivo", "zhiv* muzyk*", "живая музыка", "джаз"], "live_music", True),
    (["outdoor", "terrace", "outside", "basta", "terasa", "napolju", "на улице", "террас*"], "outdoor_seating", True),
    (["wifi", "wi-fi", "internet", "вайфай"], "wifi", True),
]
LOCAL = ["local", "traditional", "montenegrin", "domac*", "tradicionaln*", "crnogorsk*", "mjesn*",
         "местн*", "традиционн*", "черногорск*", "национальн*"]
LIVELY = ["lively", "busy", "fun", "party", "zivahn*", "zabav*", "veselo", "живое", "весел*", "оживлен*"]
QUIET = ["quiet", "calm", "relaxed", "mirn*", "tih*", "тих*", "спокойн*"]
NOW = ["now", "right now", "open now", "currently", "sada", "trenutno", "sad", "сейчас"]
AFTER_MIDNIGHT = ["after midnight", "after 12", "late night", "posle ponoci", "poslije ponoci", "после полуночи"]
NEAR = ["nearby", "near the hotel", "near here", "near me", "close by", "walking distance", "around here",
        "u blizini", "blizu", "рядом", "поблизости", "недалеко"]
SERVING = ["still serving", "kitchen open", "serving", "radi kuhinja", "kuhinja radi", "kuhinja jos radi",
           "кухня работает", "работает кухня", "работать кухня", "кухня будет работать", "кухня еще работает"]


@dataclass
class DiscoveryRequest:
    query: DiscoveryQuery
    events: bool = False
    essential: bool = False
    time_explicit: bool = False
    wants_booking: bool = False
    hits: list[str] = field(default_factory=list)


def _any(folded: str, phrases: list[str]) -> bool:
    return any(contains_phrase(folded, p) for p in phrases)


def _subcategory_hits(folded: str, excluded: set[str]) -> set[str]:
    hits = {key for key, sub in SUBCATEGORIES.items()
            if sub.keywords and _any(folded, list(sub.keywords))} - excluded
    # Prefer specific kinds over generic ones within the same family.
    if hits & {"cocktail_bar", "wine_bar", "pub", "nightclub", "live_music_venue"}:
        hits.discard("bar")
    if hits & {"konoba"}:
        hits.discard("restaurant")
    return hits


def parse_discovery(text: str, today_local: datetime, *, require_cue: bool = True) -> DiscoveryRequest | None:
    folded = fold(text)
    cue = _any(folded, CUES) or _any(folded, EVENTS) or _any(folded, MUSIC)   # events are always outside
    if require_cue and not cue:
        return None
    q = DiscoveryQuery(region="")
    req = DiscoveryRequest(query=q)

    excluded = set()
    for key, sub in SUBCATEGORIES.items():
        for kw in sub.keywords:
            if any(contains_phrase(folded, f"{neg} {kw}") for neg in EXCLUDE_PREFIX):
                excluded.add(key)
    subs = _subcategory_hits(folded, excluded)
    for generic, family in FAMILIES.items():
        if generic in subs:
            subs |= family - excluded
    cats: set[str] = set()
    for phrases, categories, subcategories in GENERIC:
        if _any(folded, phrases):
            cats |= categories
            if subcategories and not subs:
                subs |= subcategories - excluded
            if subcategories and categories == set() and subcategories <= {"supermarket", "food_market"}:
                break   # "buy food" is shopping, not a meal
    if subs and cats and not ({SUBCATEGORIES[s].category.value for s in subs} & cats):
        cats = set()   # specific kinds win over a generic family they don't belong to
    if subs:
        q.subcategories = subs
    q.categories = cats
    q.exclude_subcategories = excluded
    req.events = _any(folded, EVENTS)
    music = _any(folded, MUSIC)
    if music:
        req.events = True                      # concerts AND places with live music
        if not q.subcategories:
            q.categories |= {"NIGHTLIFE"}
    if not (q.subcategories or q.categories or req.events):
        if _any(folded, LIVELY):
            q.categories = {"NIGHTLIFE"}       # "somewhere lively"
        elif _any(folded, AFTER_MIDNIGHT) or _any(folded, ["open late", "still open"]):
            q.categories = {"FOOD", "NIGHTLIFE"}
        else:
            return None

    for phrases, attr, value in REQUIRED:
        if _any(folded, phrases) and not any(contains_phrase(folded, f"{neg} {p}")
                                             for neg in EXCLUDE_PREFIX for p in phrases):
            q.required[attr] = value
    if _any(folded, LOCAL):
        q.any_of["cuisine"] = ["montenegrin"]
        q.tags_preferred.add("local")
    if _any(folded, LIVELY):
        q.tags_preferred.add("lively")
        q.preferred["noise_level"] = "lively"
    if _any(folded, QUIET):
        q.tags_preferred.add("quiet")
        q.preferred["noise_level"] = "quiet"
    if contains_phrase(folded, "jazz") or contains_phrase(folded, "джаз"):
        q.tags_preferred.add("jazz")

    parts = parse_when(folded, today_local.date())
    if parts.day or parts.time:
        day = parts.day or today_local.date()
        q.at = datetime.combine(day, parts.time or time(20, 0))
        req.time_explicit = True
        q.open_at = True
    elif _any(folded, NOW) or _any(folded, ["open", "otvoren*", "открыт*"]):
        q.at = today_local.replace(tzinfo=None)
        q.open_at = True
    if _any(folded, AFTER_MIDNIGHT):
        base = (q.at or today_local.replace(tzinfo=None)).date()
        q.open_until = datetime.combine(base + timedelta(days=1), time(0, 30))
        q.open_at = False
    if _any(folded, SERVING):
        q.serving_at, q.open_at = True, False
    if _any(folded, NEAR):
        q.max_km = 2.0
    req.essential = bool(q.subcategories) and all(SUBCATEGORIES[s].category.value in ESSENTIAL for s in q.subcategories)
    req.wants_booking = _any(folded, ["book", "reserve", "rezervis*", "заброниру*", "забронир*"])
    req.hits = sorted(q.subcategories | q.categories)
    return req


def tonight(day: date) -> datetime:
    return datetime.combine(day, time(21, 0))
