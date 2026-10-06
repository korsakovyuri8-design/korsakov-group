"""References to shown results, and the plan commands that use them.

    "book 1" / "save #2"                      -> by number
    "book the first restaurant"               -> ordinal within a kind
    "save the second bar"                     -> ordinal within a kind
    "add the concert to Sunday"               -> the only (or first) event of that kind
    "save Bar Demo Koktel"                    -> by name
    "Book the first restaurant, save the second bar and add the concert to Sunday"
                                              -> three independent commands

Results are stored per conversation as `last_results` entries:
    {place_id | event_id, kind: place|event, category, subcategory,
     event_category, name, at, party, section}

A reference that does not match a shown result resolves to nothing - it is
never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.nlp.temporal import parse_when
from app.places.taxonomy import FAMILIES, SUBCATEGORIES
from app.text import contains_phrase, fold

VERBS: list[tuple[str, str]] = [
    # longest first: "buy tickets" before "buy", "add ... to" before "add"
    (r"buy (?:\w+ )?tickets?(?: for)?|get (?:\w+ )?tickets?(?: for)?|tickets? for|kupi(?:ti)? (?:\w+ )?(?:karte|ulaznice)|"
     r"kupi(?:t)? (?:\w+ )?bilet\w*", "tickets"),     # Russian, as folded (transliterated)
    (r"book|reserve|rezervi\w+|zabronir\w*|bronir\w*", "book"),
    (r"shortlist|short-list|uzi izbor", "shortlist"),
    (r"save|keep|remember|bookmark|sacuvaj|zapamti|sohran\w*|zapomn\w*", "save"),
    (r"add|put|plan|ubaci|dodaj|stavi|dobav\w*|zaplaniruj\w*|vnesi\w*", "plan"),
]
_VERB_RE = re.compile(r"^\s*(?:please\s+|pls\s+|and\s+|then\s+|also\s+|i\s+)?(?:can you\s+|could you\s+)?(?P<verb>"
                      + "|".join(f"(?:{v})" for v, _ in VERBS) + r")\b(?P<rest>.*)$")
_SPLIT = re.compile(r",\s*(?:and\s+|then\s+)?|;\s*|\s+(?:and|then|and then|i|a)\s+(?=(?:please\s+)?(?:"
                    + "|".join(f"(?:{v})" for v, _ in VERBS) + r")\b)")
ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
    "last": -1, "prvi": 1, "prvu": 1, "prvo": 1, "drugi": 2, "drugu": 2, "drugo": 2, "treci": 3, "trecu": 3,
    "trece": 3, "zadnji": -1, "poslednji": -1,
    # Russian, as folded (transliterated)
    "pervyj": 1, "pervuyu": 1, "pervoe": 1, "vtoroj": 2, "vtoruyu": 2, "vtoroe": 2, "tretij": 3, "tretyu": 3,
    "trete": 3, "poslednij": -1, "poslednyuyu": -1,
}
# kind words -> predicate over a stored entry
_EVENT_WORDS = {
    "concert": ["concert", "koncert", "концерт"], "festival": ["festival", "фестивал"],
    "market": ["market", "pijac", "sajam", "рынок", "ярмарк"], "sports": ["race", "match", "trka", "гонк"],
    "local_event": ["walk", "setnj", "прогулк"],
}
_GENERIC_EVENT = ["event", "show", "gig", "dogadjaj", "событи", "мероприяти"]
_FOOD_WORDS = ["restaurant", "restoran", "ресторан", "place to eat", "dinner place", "lunch place", "konoba", "tavern"]
_BAR_WORDS = ["bar", "bars", "pub", "club", "lounge", "бар", "клуб", "паб"]
_CAFE_WORDS = ["cafe", "café", "coffee", "kafic", "kafe", "кафе", "кофейн"]
_ANY_WORDS = ["one", "option", "place", "result", "opciju", "mjesto", "вариант", "место"]


@dataclass
class Ref:
    number: int | None = None        # "book 2"
    ordinal: int | None = None       # "the second ..."
    kind: str | None = None          # restaurant | bar | cafe | event | event:<category> | sub:<key> | any
    name: str | None = None          # "Bar Demo Koktel"
    text: str = ""


@dataclass
class Command:
    verb: str                        # book | save | shortlist | plan | tickets
    ref: Ref
    text: str
    day: date | None = None          # "add the concert to Sunday"
    party: int | None = None         # "get 3 tickets"
    entry: dict[str, Any] | None = None
    problem: str | None = None       # not_found | ambiguous


def _kind_of(folded: str) -> str | None:
    for cat, words in _EVENT_WORDS.items():
        if any(contains_phrase(folded, w + "*") for w in words):
            return f"event:{cat}"
    if any(contains_phrase(folded, w + "*") for w in _GENERIC_EVENT):
        return "event"
    if any(contains_phrase(folded, w) for w in _CAFE_WORDS):
        return "cafe"
    if any(contains_phrase(folded, w) for w in _FOOD_WORDS):
        return "restaurant"
    if any(contains_phrase(folded, w) for w in _BAR_WORDS):
        return "bar"
    for key, sub in SUBCATEGORIES.items():
        if sub.keywords and any(contains_phrase(folded, k) for k in sub.keywords):
            return f"sub:{key}"
    if any(contains_phrase(folded, w) for w in _ANY_WORDS):
        return "any"
    return None


def parse_ref(rest: str) -> Ref:
    folded = fold(rest).strip()
    ref = Ref(text=rest.strip())
    if m := re.match(r"^(?:#|no\.?\s*|number\s+|broj\s+|номер\s+)?(\d{1,2})\b", folded):
        ref.number = int(m.group(1))
        return ref
    words = re.findall(r"[\w'-]+", folded)
    ref.ordinal = next((ORDINALS[w] for w in words if w in ORDINALS), None)
    head = re.split(r"\b(?:to|for|on|in|za|na|v)\b", folded)[0]
    ref.kind = _kind_of(head) or _kind_of(folded)
    name = re.sub(r"^(?:the|a|an|our|my)\s+", "", rest.strip(), flags=re.I)
    name = re.split(r"\s+(?:to|for|on)\s+(?:my |our |the )?(?:plan|list|sunday|saturday|friday|monday|tuesday|"
                    r"wednesday|thursday|tonight|tomorrow)\b", name, flags=re.I)[0].strip(" .,!?")
    ref.name = name or None
    return ref


def parse_commands(text: str, today: date) -> list[Command]:
    """Every clause that starts with a plan verb. A message without any is
    not a command message."""
    parts = [p for p in _SPLIT.split(fold(text)) if p and p.strip()]
    out: list[Command] = []
    for part in parts:
        m = _VERB_RE.match(part)
        if not m:
            if re.fullmatch(r"\W*(?:please|ok(?:ay)?|great|thanks?|thank you|perfect|good|molim|hvala|"
                            r"pozhalujsta|spasibo|horosho|ok)?\W*", part):
                continue      # filler ("ok, book the first one")
            return []         # a clause that is not a command: not a command message ("what did I save?")
        verb_text = m.group("verb")
        verb = next(v for pattern, v in VERBS if re.fullmatch(pattern, verb_text))
        rest = m.group("rest")
        party = None
        if verb == "tickets" and (n := re.search(r"\b(\d{1,2}|two|three|four|five|six)\b", verb_text)):
            party = int(n.group(1)) if n.group(1).isdigit() else \
                {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}[n.group(1)]
        if verb == "plan" and not re.search(r"\b(?:to|in|into|for|on|u|na|v)\b", rest):
            continue          # "add" without a target is not a plan command ("add milk")
        when = parse_when(rest, today)
        out.append(Command(verb, parse_ref(rest), part, day=when.day if verb == "plan" else None, party=party))
    return out


# ------------------------------------------------------------- resolution
def _matches(entry: dict[str, Any], kind: str | None) -> bool:
    if kind in (None, "any"):
        return True
    is_event = entry.get("kind") == "event"
    if kind == "event":
        return is_event
    if kind.startswith("event:"):
        return is_event and entry.get("event_category") == kind.split(":", 1)[1]
    if is_event:
        return False
    sub, cat = entry.get("subcategory"), entry.get("category")
    if kind == "restaurant":
        return cat == "FOOD" and sub not in ("cafe", "bakery")
    if kind == "bar":
        return cat == "NIGHTLIFE" or sub in FAMILIES.get("bar", set())
    if kind == "cafe":
        return sub == "cafe"
    if kind.startswith("sub:"):
        key = kind.split(":", 1)[1]
        return sub == key or sub in FAMILIES.get(key, set())
    return False


def resolve(ref: Ref, entries: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    """(entry, None) or (None, problem). Never guesses."""
    if not entries:
        return None, "not_found"
    if ref.number is not None:
        return (entries[ref.number - 1], None) if 1 <= ref.number <= len(entries) else (None, "not_found")
    if ref.name and len(fold(ref.name)) >= 4:
        target = fold(ref.name)
        named = [e for e in entries if target == fold(e.get("name", "")) or
                 (len(target) >= 6 and target in fold(e.get("name", "")))]
        if len(named) == 1:
            return named[0], None
    if ref.kind is not None:
        pool = [e for e in entries if _matches(e, ref.kind)]
        if not pool:
            return None, "not_found"
        if ref.ordinal is None:
            if len(pool) == 1 or ref.kind.startswith("event"):
                return pool[0], None     # "the concert": the (first) concert shown
            return None, "ambiguous"
        idx = ref.ordinal - 1 if ref.ordinal > 0 else len(pool) - 1
        return (pool[idx], None) if 0 <= idx < len(pool) else (None, "not_found")
    if ref.name:
        target = fold(ref.name)
        named = [e for e in entries if target and (target in fold(e.get("name", "")) or
                                                   fold(e.get("name", "")) in target)]
        if len(named) == 1:
            return named[0], None
        return None, ("ambiguous" if named else "not_found")
    if ref.ordinal is not None:
        idx = ref.ordinal - 1 if ref.ordinal > 0 else len(entries) - 1
        return (entries[idx], None) if 0 <= idx < len(entries) else (None, "not_found")
    return None, "not_found"


@dataclass
class Resolved:
    commands: list[Command] = field(default_factory=list)

    @property
    def any_resolved(self) -> bool:
        return any(c.entry is not None for c in self.commands)


def resolve_all(commands: list[Command], entries: list[dict[str, Any]]) -> Resolved:
    for c in commands:
        c.entry, c.problem = resolve(c.ref, entries)
    return Resolved(commands)
