"""Iteration 6.1: "cocktails" asks for a DRINK, not a strict OSM class
(docs/ITERATION_6_1.md §3). The candidates are the whole bar family; cocktail
evidence on a place (its name, tags or attributes) only raises relevance, and
its absence excludes nothing. Real case: the frozen Budva world has 0
cocktail_bar / lounge / rooftop places but dozens of bars, one of them
"Strix Cocktail Bar" (amenity=bar) 70 m from the old town."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.discovery import engine
from app.discovery.engine import DiscoveryQuery, run
from app.discovery.nlu import parse_discovery
from app.places.taxonomy import FAMILIES
from app.shared.geo import Point

TZ = "Europe/Podgorica"
NOW = datetime(2026, 10, 8, 9, 17, tzinfo=timezone.utc)
LOCAL = datetime(2026, 10, 8, 11, 17)
ANCHOR = Point(42.2780, 18.8380)
KM = 1 / 111.2


def _q(text: str) -> DiscoveryQuery:
    return parse_discovery(text, LOCAL, require_cue=False).query


def _place(name: str, km: float, sub: str = "bar", tags: list[str] | None = None,
           attrs: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, slug=name.lower(), subcategory=sub, category="NIGHTLIFE", hours=None,
                           latitude=ANCHOR.lat + km * KM, longitude=ANCHOR.lon, timezone=None,
                           attributes=attrs or {}, tags={"tags": tags or []}, resolution={}, verification={},
                           last_verified_at=NOW, confidence=0.7, price_range=None, quality={})


def _ranked(monkeypatch, text: str, places) -> list[str]:
    q = _q(text)
    q.region, q.near, q.limit = "r", ANCHOR, 10
    monkeypatch.setattr(engine, "retrieve",
                        lambda session, query: [p for p in places if p.subcategory in query.subcategories])
    return [c.place.name for c in run(None, q, NOW, TZ).candidates]


# ------------------------------------------------------------- query construction
def test_cocktails_query_spans_the_bar_family_but_stays_specific():
    q = _q("Where can I get cocktails in Budva?")
    assert FAMILIES["bar"] <= q.subcategories                 # every kind of bar is a candidate
    assert q.specific == {"cocktail_bar"}                     # ... but only a cocktail bar is an exact match
    assert q.relevance_words == {"cocktail*", "koktel*", "коктейл*"}


def test_cocktails_adds_no_hard_constraint():
    q = _q("Where can I get cocktails in Budva?")
    assert not (q.required or q.any_of or q.exclude_attrs or q.exclude_subcategories)
    assert q.max_km is None and not q.open_at


def test_a_drink_is_unchanged_and_not_cocktail_only():
    q = _q("Where can I get a drink in Budva?")
    assert q.categories == {"NIGHTLIFE"} and not q.subcategories and not q.relevance_words


# ------------------------------------------------------------- ranking
def test_cocktail_evidence_in_the_name_ranks_above_plain_bars(monkeypatch):
    places = [_place("Plain Bar", 0.04), _place("Strix Cocktail Bar", 0.07), _place("Pub Nine", 0.05, "pub")]
    order = _ranked(monkeypatch, "Where can I get cocktails in Budva?", places)
    assert order[0] == "Strix Cocktail Bar" and set(order) == {"Strix Cocktail Bar", "Plain Bar", "Pub Nine"}


def test_cocktail_evidence_in_tags_or_attributes_counts(monkeypatch):
    places = [_place("Plain Bar", 0.04), _place("Tagged", 0.3, tags=["cocktails"]),
              _place("Attr", 0.3, attrs={"drink:cocktail": "yes"})]
    order = _ranked(monkeypatch, "Where can I get cocktails in Budva?", places)
    assert set(order[:2]) == {"Tagged", "Attr"} and order[2] == "Plain Bar"


def test_a_cocktail_bar_kind_is_an_exact_match(monkeypatch):
    places = [_place("Plain Bar", 0.04), _place("Koktel", 0.3, "cocktail_bar")]
    assert _ranked(monkeypatch, "Where can I get cocktails in Budva?", places)[0] == "Koktel"


def test_bar_kinds_are_not_relabelled(monkeypatch):
    places = [_place("Vino", 0.1, "wine_bar"), _place("Pub Nine", 0.05, "pub")]
    monkeypatch.setattr(engine, "retrieve", lambda session, query: list(places))
    q = _q("Where can I get cocktails in Budva?")
    q.region, q.near = "r", ANCHOR
    assert {c.place.subcategory for c in run(None, q, NOW, TZ).candidates} == {"wine_bar", "pub"}
