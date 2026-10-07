"""Entity resolution: does this source entity describe an existing canonical
entity? MATCH / NO_MATCH / AMBIGUOUS. Deterministic; no LLM.

A FALSE MERGE IS WORSE THAN A DUPLICATE. Every rule below errs towards
keeping records apart; AMBIGUOUS keeps them apart too and opens a review.

Name comparison works on tokens after folding (case, diacritics, Cyrillic
-> Latin, so Žabljak = Zabljak = Жабљак) with:
  * type words mapped to classes  ("konoba", "restaurant", "restoran",
    "ресторан" -> T:restaurant; "pizzeria" -> T:pizzeria) - so "Konoba Stari
    Grad" ~ "Stari Grad Restaurant", but "Pizzeria Roma" != "Restoran Roma";
  * locality words removed ("Konoba Stari Grad Žabljak" ~ "Konoba Stari Grad");
  * per-token fuzzy match (spelling variants) and a joined-string check
    ("Starigrad" ~ "Stari Grad").
A translated name ("Black Lake Café" / "Kafe Crno Jezero") does NOT match by
name; it needs corroboration (same phone, same domain, an explicit id).

EVIDENCE STATES. Every identity signal (phone, website domain, address,
explicit id) is SUPPORTS_MATCH, CONTRADICTS_MATCH or UNKNOWN - and a
missing value is UNKNOWN, never "compatible". ABSENCE OF CONTRADICTION IS
NOT POSITIVE EVIDENCE: name similarity and geography only GENERATE
candidates; on their own (with a compatible category) they give AMBIGUOUS,
never MATCH. An automatic MATCH needs at least one SUPPORTS signal.

PLACE rules (d = distance, s = name similarity):
  explicit shared id (ext:*, same_as)            MATCH  (AMBIGUOUS if d > 2 km or contradicted)
  d > 300 m                                      NO_MATCH  (chains: same brand elsewhere)
  type/category conflict                         NO_MATCH  (AMBIGUOUS if phone also shared)
  s >= 0.85 and d <= 100 m:
      a signal SUPPORTS, none CONTRADICTS        MATCH
      SUPPORTS and CONTRADICTS (mixed)           AMBIGUOUS
      CONTRADICTS only (e.g. different phones)   NO_MATCH  (two businesses, same name)
      only UNKNOWN (no contact on one side)      AMBIGUOUS
  shared phone or domain, d <= 50 m              MATCH  (any name: translations)
  shared phone or domain, d <= 200 m, s >= 0.5   MATCH
  s >= 0.6 and d <= 300 m                        AMBIGUOUS
  d <= 30 m, same subcategory                    AMBIGUOUS  (translated name, no corroboration)
  otherwise                                      NO_MATCH
EVENT rules (dt = start difference):
  shared explicit id / ticket url                MATCH (dates may differ: then start_at conflicts)
  dt > 12 h                                      NO_MATCH  (same artist, same venue, other date)
  dt <= 15 min, same venue, s >= 0.8             MATCH
  dt <= 15 min, same organizer, s >= 0.6         MATCH
  dt <= 15 min, same venue                       AMBIGUOUS (translated title)
  dt <= 60 min, same venue, s >= 0.8             AMBIGUOUS
  otherwise                                      NO_MATCH
Two or more MATCH candidates -> AMBIGUOUS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import CanonicalEntity, EntityAlias, EntityIdentifier, EntityLink, SourceEntity
from app.shared import geohash
from app.shared.geo import Point, distance_km
from app.text import fold
from app.world.normalize import NormalizedEntity

MATCH, NO_MATCH, AMBIGUOUS = "MATCH", "NO_MATCH", "AMBIGUOUS"
SUPPORTS, CONTRADICTS, UNKNOWN = "SUPPORTS_MATCH", "CONTRADICTS_MATCH", "UNKNOWN"

TYPE_CLASSES: dict[str, tuple[str, ...]] = {
    "restaurant": ("restaurant", "restoran", "restorant", "ristorante", "konoba", "tavern", "taverna", "bistro",
                   "kafana", "restoran", "restorani", "restaurace"),
    "pizzeria": ("pizzeria", "pizza", "picerija"),
    "cafe": ("cafe", "caffe", "coffee", "kafe", "kafic", "kafana-caffe", "kofejnja"),
    "bar": ("bar", "pub", "lounge", "kafebar"),
    "bakery": ("bakery", "pekara", "pekarna", "boulangerie"),
    "hotel": ("hotel", "hostel", "motel", "apartmani", "apartments", "guesthouse"),
    "pharmacy": ("pharmacy", "apoteka", "ljekarna", "apteka", "pharmacie"),
    "museum": ("museum", "muzej", "muzei"),
    "shop": ("shop", "store", "market", "prodavnica", "magazin"),
    "concert": ("concert", "koncert"),
    "festival": ("festival",),
}
_TYPE_OF = {w: cls for cls, words in TYPE_CLASSES.items() for w in words}


def _taxonomy_type_words() -> dict[str, str]:
    """Every single-word taxonomy keyword ("galerija", "gallery", "apoteka",
    "museum" ...) names a KIND of place, not a business: it is a type token,
    never name content (else "Galerija Luna" ~ "Galerija Sunce")."""
    from app.places.taxonomy import SUBCATEGORIES

    out: dict[str, str] = {}
    for key, sub in SUBCATEGORIES.items():
        for kw in list(sub.keywords) + list(sub.labels.values()):
            w = fold(kw).strip()
            if w and " " not in w and "*" not in w and len(w) > 2 and w.isalpha():
                out.setdefault(w, f"kind:{key}")
    return out


_TYPE_OF = {**_taxonomy_type_words(), **_TYPE_OF}      # curated classes win (konoba ~ restaurant)
_STOP = {"the", "a", "an", "and", "of", "i", "u", "na", "de", "la", "le", "el", "il", "da", "di", "&", "v"}
CATEGORY_GROUPS = [{"FOOD", "NIGHTLIFE"}]       # a cafe-bar may be filed under either


@dataclass
class Decision:
    outcome: str
    canonical_id: str | None = None
    score: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------ names
def name_tokens(name: str, locality: set[str] = frozenset()) -> tuple[set[str], set[str]]:
    """(core tokens, type classes)."""
    toks = [t for t in re.split(r"[^0-9a-z]+", fold(name)) if t]
    core, types = set(), set()
    for t in toks:
        if t in _TYPE_OF:
            types.add(_TYPE_OF[t])
        elif t not in _STOP and t not in locality:
            core.add(t)
    return core, types


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def name_similarity(a: str, b: str, locality: set[str] = frozenset()) -> tuple[float, bool]:
    """(similarity 0..1, type conflict)."""
    ca, ta = name_tokens(a, locality)
    cb, tb = name_tokens(b, locality)
    conflict = bool(ta and tb and not (ta & tb))
    if not ca or not cb:
        if ca or cb:
            return 0.0, conflict
        return (1.0 if ta == tb else 0.0), conflict
    matched = 0
    pool = list(cb)
    for t in sorted(ca):
        best = max(pool, key=lambda u: _ratio(t, u), default=None)
        if best is not None and (best == t or (len(t) > 3 and _ratio(t, best) >= 0.85)):
            matched += 1
            pool.remove(best)
    token_sim = 2 * matched / (len(ca) + len(cb))
    joined = _ratio("".join(sorted(ca)), "".join(sorted(cb)))
    concat = _ratio("".join(sorted(ca, key=lambda x: a.lower().find(x))), "".join(sorted(cb, key=lambda x: b.lower().find(x))))
    return max(token_sim, joined if joined >= 0.92 else 0.0, concat if concat >= 0.92 else 0.0), conflict


# --------------------------------------------------------------- profiles
@dataclass
class Profile:
    canonical_id: str
    names: list[str]
    points: list[Point]
    phones: set[str]
    domains: set[str]
    ext_ids: set[tuple[str, str]]
    subcategories: set[str]
    categories: set[str]
    starts: list[datetime]
    venues: set[str]
    organizers: set[str]
    addresses: set[str] = field(default_factory=set)                 # address keys (with a house number)
    phone_seen: dict[str, datetime] = field(default_factory=dict)    # newest observation of each phone
    coords_newest: datetime | None = None                             # newest observation of a location


def _profile(session: Session, canonical: CanonicalEntity) -> Profile:
    from app.places.taxonomy import category_of

    ses = session.scalars(select(SourceEntity).join(EntityLink, EntityLink.source_entity_id == SourceEntity.id)
                          .where(EntityLink.canonical_entity_id == canonical.id, EntityLink.active)).all()
    names = [a.alias for a in session.scalars(select(EntityAlias)
                                               .where(EntityAlias.canonical_entity_id == canonical.id))]
    prof = Profile(canonical.id, names or [canonical.canonical_name], [], set(), set(), set(), set(), set(), [],
                   set(), set())
    for se in ses:
        n = se.normalized or {}
        f = n.get("fields", {})
        seen = n.get("observed", {})
        if se.latitude is not None:
            prof.points.append(Point(se.latitude, se.longitude))
            when = _dt(seen.get("coordinates")) or se.last_seen_at
            if when is not None and (prof.coords_newest is None or when > prof.coords_newest):
                prof.coords_newest = when
        for kind, value in n.get("identifiers", []):
            if kind == "phone":
                prof.phones.add(value)
                when = _dt(seen.get("phone")) or se.last_seen_at
                if when is not None and (value not in prof.phone_seen or when > prof.phone_seen[value]):
                    prof.phone_seen[value] = when
            elif kind == "domain":
                prof.domains.add(value)
            elif kind.startswith("ext:") or kind == "ticket_url":
                prof.ext_ids.add((kind, value))
        if _address_key(f.get("address")):
            prof.addresses.add(_address_key(f.get("address")))
        if f.get("subcategory"):
            prof.subcategories.add(f["subcategory"])
            prof.categories.add(category_of(f["subcategory"]))
        if f.get("start_at"):
            prof.starts.append(datetime.fromisoformat(f["start_at"]))
        if n.get("venue_canonical"):
            prof.venues.add(n["venue_canonical"])
        if n.get("meta", {}).get("organizer"):
            prof.organizers.add(n["meta"]["organizer"])
    if not prof.points and canonical.latitude is not None:
        prof.points.append(Point(canonical.latitude, canonical.longitude))
    return prof


def _address_key(address: Any) -> str | None:
    """A street address strong enough to be identity evidence: folded tokens
    that include a house number. "Savin Kuk lower station" is a place
    description, not an address identity -> None (UNKNOWN)."""
    if not isinstance(address, str):
        return None
    tokens = re.findall(r"[a-z0-9]+", fold(address))
    return " ".join(tokens) if any(t[0].isdigit() for t in tokens) and len(tokens) >= 2 else None


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    from app.clock import as_utc

    return as_utc(datetime.fromisoformat(value) if isinstance(value, str) else value)


# Identity evidence ages too: a phone seen only years ago may have been reassigned.
CONTACT_EVIDENCE_MAX_AGE = timedelta(days=365)
# Relocation needs one location to be clearly older than the other; two
# CURRENT locations of one brand are a chain, not a move.
RELOCATION_MIN_GAP = timedelta(days=90)
RELOCATION_MAX_KM = 50.0


def _candidates(session: Session, ent: NormalizedEntity, source_id: str) -> list[CanonicalEntity]:
    """Blocking: exact identifiers, then geo cells (places) / time window (events).
    Entities already holding a record of the SAME source are never candidates
    (a source's two records are two things as far as we can tell)."""
    found: dict[str, CanonicalEntity] = {}
    same_source = (select(EntityLink.canonical_entity_id)
                   .join(SourceEntity, EntityLink.source_entity_id == SourceEntity.id)
                   .where(EntityLink.active, SourceEntity.source_id == source_id))
    strong = [(k, v) for k, v in ent.identifiers if k.startswith("ext:") or k in ("phone", "domain", "ticket_url")]
    if strong:
        conds = [and_(EntityIdentifier.kind == k, EntityIdentifier.value == v) for k, v in strong]
        rows = session.execute(
            select(CanonicalEntity)
            .join(EntityLink, EntityLink.canonical_entity_id == CanonicalEntity.id)
            .join(EntityIdentifier, EntityIdentifier.source_entity_id == EntityLink.source_entity_id)
            .where(or_(*conds), EntityLink.active, CanonicalEntity.entity_type == ent.entity_type,
                   CanonicalEntity.active, CanonicalEntity.id.not_in(same_source))).scalars()
        for c in rows:
            found[c.id] = c
    if ent.latitude is not None:
        cells = geohash.cells_for_radius(ent.latitude, ent.longitude, 0.3)
        conds = [and_(CanonicalEntity.geohash >= lo, CanonicalEntity.geohash < hi)
                 for lo, hi in map(geohash.prefix_range, cells)]
        q = select(CanonicalEntity).where(or_(*conds), CanonicalEntity.entity_type == ent.entity_type,
                                          CanonicalEntity.active, CanonicalEntity.id.not_in(same_source))
        if ent.entity_type == "EVENT" and ent.fields.get("start_at"):
            start = datetime.fromisoformat(ent.fields["start_at"])
            q = q.where(CanonicalEntity.starts_at.between(start - timedelta(days=1), start + timedelta(days=1)))
        for c in session.scalars(q):
            found[c.id] = c
    elif ent.entity_type == "EVENT" and ent.fields.get("start_at"):
        start = datetime.fromisoformat(ent.fields["start_at"])
        for c in session.scalars(select(CanonicalEntity).where(
                CanonicalEntity.entity_type == "EVENT", CanonicalEntity.active,
                CanonicalEntity.id.not_in(same_source),
                CanonicalEntity.starts_at.between(start - timedelta(hours=12), start + timedelta(hours=12)))):
            found[c.id] = c
    return list(found.values())


# ---------------------------------------------------------------- decide
def _best_name(ent: NormalizedEntity, prof: Profile, locality: set[str]) -> tuple[float, bool]:
    mine = [a for a, _, _ in ent.aliases] or ([ent.name] if ent.name else [])
    best, conflict_all = 0.0, True
    for a in mine:
        for b in prof.names:
            s, c = name_similarity(a, b, locality)
            best = max(best, s)
            conflict_all = conflict_all and c
    return best, (conflict_all and bool(mine) and bool(prof.names))


def _decide_place(ent: NormalizedEntity, prof: Profile, locality: set[str], source_id: str) -> tuple[str, float, dict]:
    from app.places.taxonomy import category_of

    ext = {(k, v) for k, v in ent.identifiers if k.startswith("ext:")} & prof.ext_ids
    p = Point(ent.latitude, ent.longitude) if ent.latitude is not None else None
    d = min((distance_km(p, q) for q in prof.points), default=None) if p else None
    s, type_conflict = _best_name(ent, prof, locality)
    phones = {v for k, v in ent.identifiers if k == "phone"}
    domains = {v for k, v in ent.identifiers if k == "domain"}
    mine = _dt(ent.observed.get("phone")) or _dt(ent.observed.get("name"))
    # a shared phone counts only if the other record's phone evidence is not stale relative to ours
    phone_eq = any(ph in prof.phones and (mine is None or prof.phone_seen.get(ph) is None
                                          or abs(mine - prof.phone_seen[ph]) <= CONTACT_EVIDENCE_MAX_AGE)
                   for ph in phones)
    phone_stale_shared = bool(phones & prof.phones) and not phone_eq
    domain_eq = bool(domains & prof.domains)
    phone_conflict = bool(phones and prof.phones and not (phones & prof.phones))
    sub = ent.fields.get("subcategory")
    cat = category_of(sub) if sub else None
    cat_conflict = bool(cat and prof.categories and cat not in prof.categories
                        and not any({cat} | prof.categories <= g for g in CATEGORY_GROUPS)
                        and "OTHER" not in prof.categories | {cat})
    addr = _address_key(ent.fields.get("address"))
    address_eq = bool(addr and addr in prof.addresses)
    # explicit evidence states: a missing value is UNKNOWN - never support
    signals = {
        "phone": SUPPORTS if phone_eq else CONTRADICTS if phone_conflict else UNKNOWN,   # stale shared: UNKNOWN
        "domain": SUPPORTS if domain_eq else UNKNOWN,
        "address": SUPPORTS if address_eq else UNKNOWN,
        "external_id": SUPPORTS if ext else UNKNOWN,
    }
    supports = sorted(k for k, v in signals.items() if v == SUPPORTS)
    contradicts = sorted(k for k, v in signals.items() if v == CONTRADICTS)
    ev = {"distance_m": None if d is None else round(d * 1000), "name_similarity": round(s, 3),
          "phone_equal": phone_eq, "phone_shared_but_stale": phone_stale_shared, "phone_conflict": phone_conflict,
          "domain_equal": domain_eq, "address_equal": address_eq, "type_conflict": type_conflict,
          "category_conflict": cat_conflict, "shared_ids": sorted(f"{k}={v}" for k, v in ext), "signals": signals}
    if ext:
        # an identifier can be wrong (copied, recycled, mis-keyed): it merges
        # only when nothing in the records contradicts it
        contradiction = (s < 0.5 and not phone_eq and not domain_eq) or type_conflict or cat_conflict or \
            phone_conflict
        if (d is not None and d > 2.0) or contradiction:
            return AMBIGUOUS, 0.5, {**ev, "rule": "shared id but the records contradict each other"}
        return MATCH, 1.0, {**ev, "rule": "shared explicit identifier"}
    if d is None:
        if (phone_eq or domain_eq or address_eq) and s >= 0.9 and not phone_conflict:
            return MATCH, 0.9, {**ev, "rule": "no coordinates: same contact and name"}
        return (AMBIGUOUS, 0.5, {**ev, "rule": "no coordinates: similar name"}) if s >= 0.9 else \
            (NO_MATCH, 0.0, ev)
    if d > 0.3:
        mine_loc = _dt(ent.observed.get("coordinates")) or mine
        moved = (mine_loc is not None and prof.coords_newest is not None
                 and abs(mine_loc - prof.coords_newest) >= RELOCATION_MIN_GAP)
        if s >= 0.85 and phone_eq and domain_eq and d <= RELOCATION_MAX_KM and moved \
                and not (type_conflict or cat_conflict):
            return MATCH, 0.85, {**ev, "rule": "relocated: same name, phone and website; one location is "
                                              "clearly older evidence"}
        return NO_MATCH, 0.0, {**ev, "rule": "too far apart (chains are separate locations)"}
    if type_conflict or cat_conflict:
        if phone_eq:
            return AMBIGUOUS, 0.4, {**ev, "rule": "different kind of business but same phone"}
        return NO_MATCH, 0.0, {**ev, "rule": "different kind of business"}
    if s >= 0.85 and d <= 0.1:
        # name + geography found the candidate; identity needs positive evidence
        if supports and not contradicts:
            return MATCH, round(0.7 + 0.3 * s, 3), {**ev, "rule": f"same name, same place, corroborated by "
                                                                  f"{', '.join(supports)}"}
        if supports:
            return AMBIGUOUS, 0.6, {**ev, "rule": f"similar name nearby; {', '.join(supports)} agree but "
                                                  f"{', '.join(contradicts)} contradict"}
        if contradicts:
            return NO_MATCH, 0.0, {**ev, "rule": f"same name nearby but {', '.join(contradicts)} contradict: "
                                                 f"two businesses"}
        return AMBIGUOUS, 0.5, {**ev, "rule": "similar name nearby; no positive identity evidence "
                                              "(a missing contact is unknown, not support)"}
    if (phone_eq or domain_eq) and d <= 0.05:
        # one shared contact can be a building / agency number: names must
        # be compatible, or TWO independent contacts must agree
        if s >= 0.5 or (phone_eq and domain_eq):
            return MATCH, 0.9, {**ev, "rule": "same contact at the same spot"}
        return AMBIGUOUS, 0.4, {**ev, "rule": "same contact at the same spot but unrelated names"}
    if (phone_eq or domain_eq) and d <= 0.2 and s >= 0.5:
        return MATCH, 0.85, {**ev, "rule": "same phone/website nearby, related name"}
    if s >= 0.6:
        return AMBIGUOUS, 0.5, {**ev, "rule": "similar name nearby, not enough evidence"}
    if d <= 0.03 and sub and sub in prof.subcategories:
        return AMBIGUOUS, 0.4, {**ev, "rule": "same spot, same kind, different names (translation?)"}
    return NO_MATCH, 0.0, ev


def _decide_event(ent: NormalizedEntity, prof: Profile, locality: set[str], source_id: str) -> tuple[str, float, dict]:
    ext = {(k, v) for k, v in ent.identifiers if k.startswith("ext:") or k == "ticket_url"} & prof.ext_ids
    start = datetime.fromisoformat(ent.fields["start_at"]) if ent.fields.get("start_at") else None
    dt = min((abs((start - s).total_seconds()) / 60 for s in prof.starts), default=None) if start else None
    s, _ = _best_name(ent, prof, locality)
    venue_same = bool(ent.meta.get("venue_canonical") and ent.meta["venue_canonical"] in prof.venues)
    if not venue_same and ent.latitude is not None and prof.points:
        venue_same = min(distance_km(Point(ent.latitude, ent.longitude), q) for q in prof.points) <= 0.1
    org_same = bool(ent.meta.get("organizer") and ent.meta["organizer"] in prof.organizers)
    ev = {"start_diff_min": None if dt is None else round(dt), "title_similarity": round(s, 3),
          "same_venue": venue_same, "same_organizer": org_same, "shared_ids": sorted(f"{k}={v}" for k, v in ext)}
    if ext:
        return MATCH, 1.0, {**ev, "rule": "shared explicit identifier"}
    if dt is None or dt > 12 * 60:
        return NO_MATCH, 0.0, {**ev, "rule": "different date/time"}
    if dt <= 15 and venue_same and s >= 0.8:
        return MATCH, 0.95, {**ev, "rule": "same title, venue and time"}
    if dt <= 15 and org_same and s >= 0.6:
        return MATCH, 0.85, {**ev, "rule": "same organizer and time, related title"}
    if dt <= 15 and venue_same:
        return AMBIGUOUS, 0.5, {**ev, "rule": "same venue and time, different title (translation?)"}
    if dt <= 60 and venue_same and s >= 0.8:
        return AMBIGUOUS, 0.5, {**ev, "rule": "same title and venue, times differ"}
    return NO_MATCH, 0.0, ev


# Counters for benchmarking / observability (process-local).
STATS = {"decisions": 0, "candidates_evaluated": 0, "match": 0, "no_match": 0, "ambiguous": 0}
# Opt-in: a list to record the candidate-set size of every decision (benchmarks).
CANDIDATE_SIZES: list[int] | None = None
# Opt-in: a list to record EVERY pairwise decision (record x candidate) - the
# real-data audit samples pairs from it. Observes only; never changes an outcome.
PAIR_LOG: list[dict[str, Any]] | None = None


def resolve(session: Session, ent: NormalizedEntity, source_id: str, locality: set[str] = frozenset()) -> Decision:
    decision = _resolve(session, ent, source_id, locality)
    STATS["decisions"] += 1
    STATS[decision.outcome.lower()] += 1
    return decision


def _resolve(session: Session, ent: NormalizedEntity, source_id: str, locality: set[str] = frozenset()) -> Decision:
    matches, ambiguous = [], []
    candidates = _candidates(session, ent, source_id)
    if CANDIDATE_SIZES is not None:
        CANDIDATE_SIZES.append(len(candidates))
    for canonical in candidates:
        STATS["candidates_evaluated"] += 1
        prof = _profile(session, canonical)
        decide = _decide_event if ent.entity_type == "EVENT" else _decide_place
        outcome, score, evidence = decide(ent, prof, locality, source_id)
        row = {"canonical_entity_id": canonical.id, "score": score, "evidence": evidence}
        if PAIR_LOG is not None:
            PAIR_LOG.append({"source_id": source_id, "record_id": ent.record_id, "canonical_entity_id": canonical.id,
                             "candidates": len(candidates), "outcome": outcome, **row})
        if outcome == MATCH:
            matches.append(row)
        elif outcome == AMBIGUOUS:
            ambiguous.append(row)
    if len(matches) == 1 and not ambiguous:
        m = matches[0]
        return Decision(MATCH, m["canonical_entity_id"], m["score"], m["evidence"])
    if len(matches) == 1 and ambiguous:
        m = matches[0]
        # a clear match plus weaker look-alikes: still a match, the look-alikes are recorded
        return Decision(MATCH, m["canonical_entity_id"], m["score"], {**m["evidence"], "also_similar": ambiguous})
    if matches or ambiguous:
        return Decision(AMBIGUOUS, None, 0.0, {"rule": "several candidates" if len(matches) > 1 else "uncertain"},
                        candidates=matches + ambiguous)
    return Decision(NO_MATCH)
