"""Traveller preferences: only what the traveller states as a GENERAL habit.

    "I generally prefer vegetarian places"   -> stored (vegetarian_options)
    "We usually like quiet bars"             -> stored (noise_level = quiet)
    "I'm vegan"                              -> stored (vegan_options)
    "Find vegan food tonight"                -> NOT stored (a one-off request
                                                constraint, applied as HARD
                                                to that request only)

Stored preferences are SOFT: they re-rank, never filter. A need that must
hold ("one of us is coeliac") is stated per request and is then a hard
constraint of that request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TravelerPreference
from app.text import contains_phrase, fold

# A generality marker in the same clause turns a constraint word into a habit.
_GENERAL = ["generally", "usually", "in general", "always", "normally", "typically", "as a rule", "tend to",
            "obicno", "uglavnom", "uvijek", "inace", "обычно", "всегда", "как правило", "в целом", "вообще"]
# Identity statements are durable by nature ("I'm vegetarian").
_IDENTITY = re.compile(r"\b(?:i am|i'm|im|we are|we're|were) (?:a |an |both |all )?"
                       r"(vegetarian|vegan|coeliac|celiac|halal|kosher)s?\b|"
                       r"\bja sam (vegetarijan\w*|vegan\w*)|\bя (вегетариан\w*|веган\w*)|\bмы (вегетариан\w*|веган\w*)")
_PREF_VERBS = ["prefer*", "like", "love", "enjoy", "avoid", "don't like", "do not like", "hate",
               "volim", "preferiram", "izbjegavam", "предпочита*", "люблю", "любим", "избега*", "не люб*"]

# word -> (attribute, value). Diet words, ambience, budget.
VOCAB: list[tuple[list[str], str, Any]] = [
    (["vegetarian", "vegetarijansk*", "vegetarijan*", "вегетариан*"], "vegetarian_options", True),
    (["vegan", "vegansk*", "веган*"], "vegan_options", True),
    (["gluten free", "gluten-free", "coeliac", "celiac", "bez glutena", "без глютена"], "gluten_free_options", True),
    (["halal", "халяль"], "halal", True),
    (["kosher", "кошер*"], "kosher", True),
    (["quiet", "calm", "mirn*", "tih*", "тих*", "спокойн*"], "noise_level", "quiet"),
    (["lively", "busy", "loud", "noisy", "zivahn*", "bucn*", "оживлен*", "шумн*"], "noise_level", "lively"),
    (["outdoor", "terrace", "terasa", "basta", "террас*"], "outdoor_seating", True),
    (["local food", "local places", "traditional", "domac*", "местн*", "традиционн*"], "local", True),
    (["cheap", "budget", "inexpensive", "jeftin*", "недорог*", "дешев*"], "price", "low"),
]
_NEGATIVE = ["avoid", "don't like", "do not like", "hate", "izbjegavam", "избега*", "не люб*"]


@dataclass(frozen=True)
class Stated:
    key: str
    value: Any
    statement: str


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[.;!?\n]", text) if c.strip()]


def extract(text: str) -> list[Stated]:
    """Explicit, general preference statements only."""
    out: list[Stated] = []
    for clause in _clauses(text):
        folded = fold(clause)
        identity = _IDENTITY.search(folded)
        general = any(contains_phrase(folded, g) for g in _GENERAL) and \
            any(contains_phrase(folded, v) for v in _PREF_VERBS + ["eat", "jedem", "ем", "едим"])
        if not identity and not general:
            continue
        negative = any(contains_phrase(folded, n) for n in _NEGATIVE)
        for words, attr, value in VOCAB:
            if any(contains_phrase(folded, w) for w in words):
                if negative:
                    if attr == "noise_level":   # "we avoid loud places" -> quiet
                        value = "quiet" if value == "lively" else "lively"
                    else:
                        continue                # "I avoid vegan places": not a preference we can rank on
                out.append(Stated(attr, value, clause))
    return list({s.key: s for s in out}.values())


def save(session: Session, guest_id: str, stated: list[Stated], message_id: str | None) -> list[TravelerPreference]:
    rows = []
    for s in stated:
        row = session.scalar(select(TravelerPreference).where(TravelerPreference.guest_id == guest_id,
                                                              TravelerPreference.key == s.key))
        if row is None:
            row = TravelerPreference(guest_id=guest_id, key=s.key)
            session.add(row)
        row.value, row.statement, row.source_message_id = {"value": s.value}, s.statement[:500], message_id
        rows.append(row)
    session.flush()
    return rows


def load(session: Session, guest_id: str) -> dict[str, Any]:
    return {p.key: (p.value or {}).get("value")
            for p in session.scalars(select(TravelerPreference).where(TravelerPreference.guest_id == guest_id))}


def as_ranking(prefs: dict[str, Any]) -> tuple[dict[str, Any], str | None, set[str]]:
    """Stored preferences -> (attribute preferences, price preference, tags).
    All soft."""
    attrs = {k: v for k, v in prefs.items() if k not in ("local", "price")}
    tags = {"local"} if prefs.get("local") else set()
    return attrs, prefs.get("price"), tags
