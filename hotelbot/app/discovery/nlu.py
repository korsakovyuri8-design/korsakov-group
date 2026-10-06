"""UNDERSTAND: a guest's words -> structured DiscoveryQuery (en / cnr / ru).

Deterministic. It builds a query; it never produces candidates. Constraints
are classified explicitly:

    HARD  "must be vegan", "one of us is vegan", "wheelchair accessible",
          "not a club", "not formal", "we're six", "we have a 16-year-old",
          "cheap", "open now", "within 1 km"            -> filters
    SOFT  "prefer vegetarian", "quiet if possible", "somewhere lively",
          "for a date", "where young locals go", "nice"  -> ranking only

A compound message ("local dinner and somewhere lively for drinks
afterwards") becomes several independent requests. Property questions
("What time is breakfast?") return nothing - they stay with the pack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.discovery.engine import DiscoveryQuery
from app.nlp.temporal import parse_count, parse_when
from app.places.taxonomy import FAMILIES, SUBCATEGORIES, Category
from app.text import contains_phrase, fold

ESSENTIAL = {Category.HEALTH.value, Category.FINANCIAL_SERVICE.value, Category.ESSENTIAL_SERVICE.value,
             Category.CONNECTIVITY.value, Category.MOBILITY_INFRASTRUCTURE.value}

# Explicit "look outside the property" cues.
CUES = [
    "find", "where can", "where is", "where are", "where do", "where to", "where should", "recommend*", "suggest*",
    "nearby", "near the hotel", "near here", "near me", "near us", "close by", "around here", "in town", "somewhere",
    "any good", "anything", "open now", "still open", "what's open", "whats open", "is open", "open late",
    "open after", "open today", "go for", "place to", "places to", "what can we", "what's happening",
    "whats happening", "happening tonight", "i need a", "i need an", "i need to", "we need a", "looking for",
    "get tickets", "can we see", "nearest", "where do locals", "what to do",
    "gdje", "nadji", "pronadji", "preporu*", "u blizini", "u gradu", "negdje", "otvoren*", "sta je otvoreno",
    "near", "treba mi", "trebamo", "trazim", "sta se desava", "sta ima", "najbliz*",
    "где", "найди", "найдите", "посоветуй*", "порекоменд*", "рядом", "поблизости", "в городе", "куда",
    "открыт*", "нужна", "нужен", "нужно", "ищу", "что происходит", "что посмотреть", "ближайш*",
]
# Generic needs -> whole categories / groups of subcategories.
GENERIC: list[tuple[list[str], set[str], set[str], str]] = [
    (["buy food", "groceries", "grocery", "where locals buy", "kupiti hranu", "namirnic*", "купить продукт*",
      "купить еду", "продукты"], set(), {"supermarket", "food_market"}, "groceries"),
    (["cocktail*", "koktel*", "коктейл*"], set(), {"cocktail_bar", "lounge", "rooftop"}, "drinks"),
    (["drinks", "drink", "go out", "night out", "nightlife", "izlazak", "izaci", "popiti", "pice",
      "выпить", "ночн* жизн*", "тусов*"], {"NIGHTLIFE"}, set(), "drinks"),
    (["lunch", "rucak", "rucat", "обед*"], set(), {"restaurant", "konoba", "fast_food", "cafe", "bakery"}, "lunch"),
    (["breakfast", "dorucak", "завтрак*"], set(), {"cafe", "bakery", "restaurant"}, "breakfast"),
    (["dinner", "eat", "food", "vecera", "veceru", "vecerati", "jesti", "hrana",
      "ужин*", "поесть", "еда", "кухн*"], set(), {"restaurant", "konoba", "fast_food"}, "food"),
    (["see", "visit", "sightseeing", "attraction*", "what to do", "things to do", "what can we do", "posjet*",
      "vidjeti", "znamenit*", "посмотреть", "посетить", "достопримечательн*"],
     {"ATTRACTION", "CULTURE", "NATURE"}, set(), "sights"),
    (["nature", "outdoors", "walk", "priroda", "setnja", "природ*", "погулять"], {"NATURE"}, set(), "nature"),
    (["relax", "wellness", "pamper", "opustiti", "расслаб*"], {"WELLNESS"}, set(), "wellness"),
    (["work for", "place to work", "somewhere to work", "work from", "laptop", "raditi", "поработать"],
     set(), {"coworking", "cafe"}, "work"),
]
EVENTS = ["event*", "happening", "concert*", "festival*", "show", "gig", "what's on", "whats on", "anything on",
          "interesting happening", "dogadjaj*", "koncert*", "festival*", "desava", "событи*", "концерт*",
          "фестивал*", "мероприяти*", "происходит"]
MUSIC = ["live music", "jazz", "svirka", "muzika uzivo", "dzez", "живая музыка", "живой музык*", "джаз"]
EVENT_CATEGORY_WORDS = {
    "concert": ["concert*", "gig", "koncert*", "концерт*"], "festival": ["festival*", "фестивал*"],
    "market": ["market", "fair", "pijac*", "sajam", "ярмарк*", "рынок"], "sports": ["match", "game", "sport*", "матч*"],
    "exhibition": ["exhibition*", "izlozb*", "выставк*"], "theater": ["play", "theatre", "theater", "спектакл*"],
}
EXCLUDE_PREFIX = ["not a", "not an", "no", "without", "but not", "except", "ne", "nije", "bez", "не", "без"]
SOFT_MARKERS = ["prefer*", "ideally", "if possible", "would be nice", "nice to have", "preferably", "bonus",
                "po mogucnosti", "pozeljno", "bilo bi lijepo", "желательно", "по возможности", "предпочтительно",
                "лучше бы"]

REQUIRED = [
    (["vegetarian", "vegetarijansk*", "вегетариан*"], "vegetarian_options", True),
    (["vegan", "vegansk*", "веган*"], "vegan_options", True),
    (["gluten free", "gluten-free", "coeliac", "celiac", "bez glutena", "без глютена"], "gluten_free_options", True),
    (["halal", "халяль", "халал"], "halal", True),
    (["kosher", "кошер*"], "kosher", True),
    (["wheelchair", "accessible", "step-free", "invalidsk* kolic*", "pristupacn*", "инвалидн* коляск*",
      "доступн* для"], "wheelchair_access", True),
    (["kid friendly", "child friendly", "family friendly", "za djecu", "для детей"], "child_friendly", True),
    (["dog", "pet", "pets", "pas", "psom", "ljubim*", "собак*", "питом*"], "pets_allowed", True),
    (["outdoor", "terrace", "outside", "basta", "terasa", "napolju", "на улице", "террас*"], "outdoor_seating", True),
    (["wifi", "wi-fi", "internet", "вайфай"], "wifi", True),
    (["takeaway", "take away", "to go", "za poneti", "навынос", "с собой"], "takeaway_supported", True),
    (["delivery", "deliver", "dostav*", "доставк*"], "delivery_supported", True),
]
LOCAL = ["local", "traditional", "montenegrin", "domac*", "tradicionaln*", "crnogorsk*", "mjesn*",
         "местн*", "традиционн*", "черногорск*", "национальн*"]
LIVELY = ["lively", "busy", "fun", "party", "buzzing", "zivahn*", "zabav*", "veselo", "живое", "весел*", "оживлен*"]
QUIET = ["quiet", "calm", "relaxed", "cosy", "cozy", "mirn*", "tih*", "тих*", "спокойн*", "уютн*"]
DATE = ["for a date", "romantic", "date night", "romanticn*", "романтич*", "свидани*"]
YOUNG = ["young", "younger", "students", "mlad*", "молодеж*", "молод*"]
LOCALS = ["locals go", "local crowd", "where locals", "lokalci", "мести* жител*", "местные"]
CHEAP = ["cheap", "budget", "inexpensive", "not expensive", "jeftin*", "povoljn*", "дешев*", "недорог*", "бюджетн*"]
NICE = ["nice", "fancy", "upscale", "special", "fine dining", "otmjen*", "изыскан*", "хорош* ресторан"]
NOT_FORMAL = ["not formal", "not too formal", "casual", "relaxed", "nothing fancy", "ne formalno", "lezern*",
              "неформальн*", "без дресс-кода", "простое"]
NOW = ["now", "right now", "open now", "currently", "at the moment", "sada", "trenutno", "сейчас"]
AFTER_MIDNIGHT = ["after midnight", "after 12", "late night", "posle ponoci", "poslije ponoci", "после полуночи"]
NEAR = ["nearby", "near the hotel", "near here", "near me", "near us", "close by", "around here",
        "u blizini", "blizu", "рядом", "поблизости", "недалеко"]
SERVING = ["still serving", "kitchen open", "kitchen still open", "serving", "serves food", "radi kuhinja",
           "kuhinja radi", "kuhinja jos radi", "кухня работает", "работает кухня", "работать кухня",
           "кухня будет работать", "кухня еще работает"]
_AFTER_HOUR = re.compile(r"\b(?:open\s+)?after\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b|\bposlije\s+(\d{1,2})\b|"
                         r"\bposle\s+(\d{1,2})\b|\bpos[lj]e\s+(\d{1,2})\b")
_RADIUS_KM = re.compile(r"\bwithin\s+(\d+(?:[.,]\d+)?)\s*(km|kilomet\w*|m|meters|metres)\b|"
                        r"\b(?:u krugu|do)\s+(\d+(?:[.,]\d+)?)\s*(km|m)\b|\bв радиусе\s+(\d+(?:[.,]\d+)?)\s*(км|м)\b")
_RADIUS_WALK = re.compile(r"\bwithin\s+(\d{1,2})\s*(?:min\w*)\s*(?:walk\w*)?|\b(\d{1,2})\s*min\w*\s+walk")
_AGE = re.compile(r"\b(\d{1,2})[\s-]*(?:year[\s-]*old|yo|godin\w*|лет\w*|год\w*)\b")
_HOURS_FOR = re.compile(r"\bfor\s+(\d{1,2}|two|three|four|five|six)\s+hours?\b|\bna\s+(\d{1,2})\s+sat\w*\b|"
                        r"\bна\s+(\d{1,2}|два|три|четыре)\s+час\w*\b")
_NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "два": 2, "три": 3, "четыре": 4}


@dataclass
class DiscoveryRequest:
    query: DiscoveryQuery
    events: bool = False
    essential: bool = False
    time_explicit: bool = False
    wants_booking: bool = False
    recommend: bool = False             # RECOMMEND (judgement asked) vs FIND
    label: str | None = None            # "food" | "drinks" | "lunch" | "groceries" | ...
    event_categories: set[str] = field(default_factory=set)
    event_window: tuple[datetime, datetime] | None = None
    anchor_text: str | None = None      # "the Black Lake" in "near the Black Lake"
    hits: list[str] = field(default_factory=list)
    text: str = ""


def _any(folded: str, phrases: list[str]) -> bool:
    return any(contains_phrase(folded, p) for p in phrases)


def _soft(folded: str, phrase_hit: str) -> bool:
    """Is the constraint word in a softening clause ("vegetarian if possible")?"""
    for clause in re.split(r"[,.;!?]| but | and | i | и ", folded):
        if contains_phrase(clause, phrase_hit) and _any(clause, SOFT_MARKERS):
            return True
    return False


def _party(folded: str) -> int | None:
    """Group size: the largest count stated ("we're six and one is vegan" -> 6)."""
    from app.nlp.temporal import _COUNT_RES, _to_int

    counts = [n for pattern in _COUNT_RES for m in pattern.finditer(folded) if (n := _to_int(m.group(1)))]
    return max(counts) if counts else parse_count(folded)


def _names_a_kind(folded: str) -> bool:
    """A short message that names a kind of place ("A cocktail bar for a
    date") is a discovery request even without "find"."""
    if _PROPERTY_QUESTION.match(folded) or _any(folded, ["at the hotel", "in the hotel", "u hotelu", "в отеле"]):
        return False                    # "Do you have a sauna?" asks about the property
    return len(folded.split()) <= 12 and bool(_subcategory_hits(folded, set()))


_PROPERTY_QUESTION = re.compile(r"^\s*(do you|does the (hotel|property|apartment)|have you got|is there |are there |"
                                r"can i (use|book) (the|your)|what time (is|does)|when (is|does))")


def _subcategory_hits(folded: str, excluded: set[str]) -> set[str]:
    hits = {key for key, sub in SUBCATEGORIES.items()
            if sub.keywords and _any(folded, list(sub.keywords))} - excluded
    if hits & {"cocktail_bar", "wine_bar", "pub", "nightclub", "live_music_venue", "rooftop", "lounge"}:
        hits.discard("bar")
    if hits & {"konoba"}:
        hits.discard("restaurant")
    return hits


def _excluded(folded: str) -> set[str]:
    out = set()
    for key, sub in SUBCATEGORIES.items():
        for kw in sub.keywords:
            if any(contains_phrase(folded, f"{neg} {kw}") for neg in EXCLUDE_PREFIX):
                out.add(key)
    return out


def parse_discovery(text: str, today_local: datetime, *, require_cue: bool = True) -> DiscoveryRequest | None:
    folded = fold(text)
    cue = _any(folded, CUES) or _any(folded, EVENTS) or _any(folded, MUSIC) or _names_a_kind(folded)
    if require_cue and not cue:
        return None
    q = DiscoveryQuery(region="")
    req = DiscoveryRequest(query=q, text=text)
    today_naive = today_local.replace(tzinfo=None)

    # ---------------------------------------------------------- what kind
    anchor = re.search(r"\bnear (?:the )?([a-z][a-z' ]{2,40}?)(?:[,.?!]|$| and | for | open)", folded)
    kinds_text = folded
    if anchor and anchor.group(1).strip() not in ("me", "us", "here", "hotel", "the hotel", "our hotel", "you"):
        kinds_text = folded.replace(anchor.group(0), " ")   # "near the Black Lake" names the anchor, not the kind
    excluded = _excluded(kinds_text)
    subs = _subcategory_hits(kinds_text, excluded)
    q.specific = set(subs)
    for generic, family in FAMILIES.items():
        if generic in subs:
            subs |= family - excluded
    cats: set[str] = set()
    for phrases, categories, subcategories, label in GENERIC:
        if _any(kinds_text, phrases):
            req.label = req.label or label
            cats |= categories
            # "cocktails" names a DRINK: every kind of place that serves it
            # (cocktail bar, lounge, rooftop), even though "cocktail" also
            # matches the cocktail_bar keyword.
            if subcategories and (label in ("drinks", "work") or not subs):
                subs |= subcategories - excluded
            if label in ("groceries",):
                break   # "buy food" is shopping, not a meal
    if subs and cats and not ({SUBCATEGORIES[s].category.value for s in subs if s in SUBCATEGORIES} & cats) \
            and req.label not in ("sights", "work"):
        cats = set()   # specific kinds win over a generic family they don't belong to
    q.subcategories, q.categories, q.exclude_subcategories = subs, cats, excluded
    req.events = _any(folded, EVENTS)
    req.event_categories = {c for c, words in EVENT_CATEGORY_WORDS.items() if _any(folded, words)}
    music = _any(folded, MUSIC)
    if music:
        req.events = True                      # concerts AND places with live music
        q.relevance_tags |= {"jazz"} if _any(folded, ["jazz", "dzez", "джаз"]) else set()
        q.required["live_music"] = True
        if not q.subcategories:
            q.categories |= {"NIGHTLIFE"}
    if req.events and not (q.subcategories or q.categories) and not music:
        pass   # an events-only request ("anything happening Sunday evening?")
    elif not (q.subcategories or q.categories or req.events):
        if _any(folded, CHEAP):
            q.categories, req.label = {"FOOD"}, "food"   # "something cheap and open now"
        elif _any(folded, LIVELY):
            q.categories = {"NIGHTLIFE"}       # "somewhere lively"
        elif _any(folded, AFTER_MIDNIGHT) or _any(folded, ["open late", "still open"]) or _AFTER_HOUR.search(folded):
            q.categories = {"FOOD", "NIGHTLIFE"}
        else:
            return None

    # --------------------------------------------- hard vs soft attributes
    for phrases, attr, value in REQUIRED:
        hit = next((p for p in phrases if contains_phrase(folded, p)), None)
        if hit is None or any(contains_phrase(folded, f"{neg} {p}") for neg in EXCLUDE_PREFIX for p in phrases):
            continue
        if _soft(folded, hit):
            q.preferred[attr] = value
        else:
            q.required[attr] = value
    if _any(folded, LOCAL):
        q.relevance_tags.add("local")
        if q.subcategories & FAMILIES["restaurant"] or req.label in ("food", "lunch"):
            q.any_of["cuisine"] = ["montenegrin"]
    if _any(folded, LIVELY):
        q.tags_preferred.add("lively")
        q.preferred["noise_level"] = "lively"
    if _any(folded, QUIET):
        q.tags_preferred.add("quiet")
        q.preferred["noise_level"] = "quiet"
    if _any(folded, DATE):
        q.tags_preferred |= {"romantic", "quiet"}
    if _any(folded, YOUNG):
        q.preferred["crowd_profile"] = "young"
    if _any(folded, LOCALS):
        q.tags_preferred.add("local")
    if _any(folded, NOT_FORMAL):
        q.exclude_attrs["dress_code"] = {"formal", "smart"}
        q.tags_preferred.add("casual")
    if _any(folded, CHEAP):
        if _any(folded, ["not too expensive", "not expensive", "reasonabl*"]):
            q.price_pref = "low"
        else:
            q.price_max, q.price_pref = 1, "low"
    elif _any(folded, NICE):
        q.price_pref = "high"
    if req.label == "work":
        q.preferred.setdefault("wifi", True)    # a cafe to work from: Wi-Fi ranks first (soft)
    if _any(folded, ["romantic", "for a date"]) and "cocktail_bar" in q.subcategories:
        q.specific.add("cocktail_bar")

    # ------------------------------------------- group, ages, family, pets
    party = _party(folded)
    if party:
        q.party_size = party
    ages = [int(a) for a in _AGE.findall(folded)]
    if ages:
        q.min_age = min(ages)
    elif _any(folded, ["with kids", "with children", "with the kids", "kids", "children", "sa djecom", "s djecom",
                       "с детьми", "с ребенком"]):
        q.min_age = 10   # a child of unstated age: no age-restricted venues
        q.tags_preferred.add("family")

    # ------------------------------------------------------- time
    parts = parse_when(folded, today_local.date())
    if parts.day or parts.time:
        day = parts.day or today_local.date()
        q.at = datetime.combine(day, parts.time or _default_time(req.label))
        req.time_explicit = True
        q.open_at = True
    elif _any(folded, NOW) or _any(folded, ["open", "otvoren*", "открыт*"]):
        q.at = today_naive
        q.open_at = True
    if req.label in ("lunch", "food") and (q.at is None or parts.approximate):
        meal_at = _meal_time(folded, parts, q.at, today_naive)
        if meal_at is not None:                 # "for dinner" = this evening's dinner time, not "any time"
            q.at = meal_at
    if req.label in ("lunch", "food") and q.at is not None and _any(folded, ["lunch", "dinner", "eat", "rucak",
                                                                             "vecer*", "обед*", "ужин*", "food",
                                                                             "tonight"]):
        q.serving_at, q.open_at = True, False   # a meal: the KITCHEN must be serving
    if _any(folded, AFTER_MIDNIGHT):
        base = (q.at or today_naive).date()
        q.open_until = datetime.combine(base + timedelta(days=1), time(0, 30))
        q.open_at = False
    if (m := _AFTER_HOUR.search(folded)) and not _any(folded, AFTER_MIDNIGHT):
        hour = int(next(g for g in m.groups() if g and g.isdigit()))
        minute = int(m.group(2)) if m.group(2) else 0
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        base = (q.at or today_naive).date()
        when = datetime.combine(base + (timedelta(days=1) if hour < 7 else timedelta()), time(hour % 24, minute))
        q.open_until, q.open_at = when, False
        if q.at is not None and q.at.time() == when.time():
            q.at = today_naive          # "after 1am" was the time itself: today's state is shown
    if _any(folded, SERVING):
        q.serving_at, q.open_at = True, False
        if q.at is None:
            q.at = today_naive
    if (m := _HOURS_FOR.search(folded)) and req.label in ("work", None) or (m and "coworking" in q.subcategories):
        n = next(g for g in m.groups() if g)
        hours = int(n) if n.isdigit() else _NUM.get(n, 2)
        start = q.at or today_naive
        q.at, q.window_end, q.open_at = start, start + timedelta(hours=hours), True

    # ----------------------------------------------------------- where
    if _any(folded, NEAR):
        q.max_km = q.max_km or (2.0 if not _any(folded, ["near me", "near us"]) else None)
    if m := _RADIUS_KM.search(folded):
        val, unit = next((m.group(i), m.group(i + 1)) for i in (1, 3, 5) if m.group(i))
        km = float(val.replace(",", "."))
        q.max_km = km / 1000 if unit in ("m", "meters", "metres", "м") else km
    elif m := _RADIUS_WALK.search(folded):
        from app.shared.geo import km_for_walk

        q.max_km = km_for_walk(int(m.group(1) or m.group(2)))
    elif _any(folded, ["walking distance", "on foot", "pjeske", "пешком"]):
        from app.shared.geo import km_for_walk

        q.max_km = km_for_walk(15)
    if m := re.search(r"\bnear (?:the )?([a-z][a-z' ]{2,40}?)(?:[,.?!]|$| and | for | open)", folded):
        target = m.group(1).strip()
        if target not in ("me", "us", "here", "hotel", "the hotel", "our hotel", "you"):
            req.anchor_text = target

    # --------------------------------------------------- events window
    if req.events:
        day = parts.day or (q.at.date() if q.at else today_local.date())
        start_h, end_h = 0, 24
        if _any(folded, ["evening", "tonight", "uvece", "veceras", "вечер*"]):
            start_h = 17
        elif _any(folded, ["morning", "ujutru", "утр*"]):
            end_h = 12
        elif _any(folded, ["afternoon", "popodne", "днем"]):
            start_h, end_h = 12, 18
        if parts.day or q.at or start_h or end_h != 24:
            req.event_window = (datetime.combine(day, time(0)) + timedelta(hours=start_h),
                                datetime.combine(day, time(0)) + timedelta(hours=end_h))

    subs_known = [s for s in q.subcategories if s in SUBCATEGORIES]
    req.essential = bool(subs_known) and all(SUBCATEGORIES[s].category.value in ESSENTIAL for s in subs_known)
    q.essential = req.essential
    if req.essential and q.at is None and _any(folded, NOW):
        q.at = today_naive
    req.wants_booking = _any(folded, ["book", "reserve", "rezervis*", "заброниру*", "забронир*"])
    req.recommend = _any(folded, ["recommend*", "suggest*", "best", "good", "preporu*", "посоветуй*",
                                  "порекоменд*", "лучш*"]) and not req.essential
    req.hits = sorted(q.subcategories | q.categories)
    return req


_DINNER = ["dinner", "supper", "vecer*", "ужин*"]
_LUNCH = ["lunch", "rucak", "rucat", "обед*"]


def _meal_time(folded: str, parts, at: datetime | None, now: datetime) -> datetime | None:  # noqa: ANN001
    """A meal named without a clock time: dinner -> 20:00, lunch -> 13:00
    (or now, if that has passed but the meal is still on). A meal already
    over today is not moved to tomorrow silently: no time is set."""
    day = at.date() if (at is not None and parts.day) else now.date()
    evening = at is not None and at.hour >= 17
    if _any(folded, _DINNER) or evening:
        target, last = time(20, 0), time(22, 30)
    elif _any(folded, _LUNCH) or (at is not None and 11 <= at.hour < 16):
        target, last = time(13, 0), time(15, 0)
    else:
        return None
    when = datetime.combine(day, target)
    if day == now.date() and when < now:
        return now if now.time() <= last else None
    return when


def _default_time(label: str | None) -> time:
    return {"lunch": time(13, 0), "breakfast": time(9, 0), "drinks": time(22, 0)}.get(label or "", time(20, 0))


# ------------------------------------------------------------- compound
_JOIN = re.compile(r"(?:,\s*(?:and\s+)?|\s+(?:and|and then|then|plus|i|pa|а потом|и|затем)\s+)")


def understand(text: str, today_local: datetime) -> list[DiscoveryRequest]:
    """One request per discovery need in the message. "Somewhere local for
    dinner and somewhere lively for drinks afterwards" -> two requests; the
    second inherits the day and starts after the first."""
    parts = [p for p in _JOIN.split(text) if p and p.strip()]
    chunks: list[str] = []
    for p in parts:
        if chunks and parse_discovery(p, today_local, require_cue=False) is None:
            chunks[-1] += " " + p      # no need of its own: belongs to the previous part
        else:
            chunks.append(p)
    reqs = [r for r in (parse_discovery(c, today_local, require_cue=False) for c in chunks) if r is not None]
    if len(reqs) < 2 or not parse_discovery(text, today_local):
        single = parse_discovery(text, today_local)
        return [single] if single else []
    labels = [r.label for r in reqs if r.label]
    kinds = [r.query.subcategories | r.query.categories for r in reqs]
    overlap = any(kinds[i] & kinds[j] for i in range(len(kinds)) for j in range(i + 1, len(kinds)))
    if len(set(labels)) < len(labels) or overlap:   # the same need twice -> it was one request
        single = parse_discovery(text, today_local)
        return [single] if single else []
    for prev, cur in zip(reqs, reqs[1:], strict=False):
        if cur.query.at is None and prev.query.at is not None:
            later = _any(fold(cur.text), ["afterwards", "after", "later", "then", "poslije", "potom", "потом",
                                          "после"])
            cur.query.at = prev.query.at + timedelta(hours=2) if later else prev.query.at
            cur.query.open_at = cur.query.open_at or later
    return reqs


def tonight(day: date) -> datetime:
    return datetime.combine(day, time(21, 0))
