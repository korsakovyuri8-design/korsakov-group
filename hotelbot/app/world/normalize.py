"""SOURCE RECORD -> NORMALIZED SOURCE ENTITY.

Separate from transport (adapters) so the same rules apply to every source.
Normalizes names, phones (E.164 when the country is known), URLs/domains,
coordinates, addresses, category taxonomy, opening hours, currencies,
languages, event dates and price ranges. The raw record is kept by the
pipeline for audit; here, a value that cannot be normalized is REPORTED in
`issues` and not asserted - never guessed (no default country code, no
default opening hours, no midnight for a missing event time).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.text import fold
from app.world.adapters import SourceDescriptor, SourceRecord

CALLING_CODES = {"ME": "382", "RS": "381", "HR": "385", "BA": "387", "AL": "355", "FR": "33", "DE": "49",
                 "GB": "44", "US": "1", "JP": "81", "GE": "995", "IT": "39", "ES": "34", "AT": "43"}
# Domains shared by many businesses: never an identity signal.
PLATFORM_DOMAINS = {"facebook.com", "instagram.com", "booking.com", "tripadvisor.com", "google.com",
                    "linktr.ee", "wixsite.com", "business.site", "yelp.com"}
_DAYS = ["mo", "tu", "we", "th", "fr", "sa", "su"]
_DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIME = re.compile(r"^([01]?\d|2[0-4]):([0-5]\d)$")


@dataclass
class NormalizedEntity:
    entity_type: str                                 # PLACE | EVENT | TAXONOMY
    record_id: str
    fields: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, datetime] = field(default_factory=dict)       # per-field observation date
    identifiers: list[tuple[str, str]] = field(default_factory=list)  # (kind, value)
    aliases: list[tuple[str, str | None, str]] = field(default_factory=list)   # (alias, language, kind)
    latitude: float | None = None
    longitude: float | None = None
    venue_ref: str | None = None                     # same-source record id of the venue (events)
    slug_hint: str | None = None
    region_hint: str | None = None
    confidence: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def name(self) -> str | None:
        v = self.fields.get("name") or self.fields.get("title")
        if isinstance(v, dict):
            return v.get("en") or next(iter(v.values()), None)
        return v


# ------------------------------------------------------------- primitives
def clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", re.sub(r"\s+", " ", value)).strip()
    return text or None


def normalize_phone(value: Any, country: str | None) -> tuple[str | None, str | None]:
    """(E.164 or None, issue or None). Never invents a country code."""
    if not isinstance(value, str) or not value.strip():
        return None, None
    raw = value.strip()
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+"):
        body = re.sub(r"\D", "", digits)
        return ("+" + body, None) if 7 <= len(body) <= 15 else (None, f"phone not parseable: {raw!r}")
    code = CALLING_CODES.get((country or "").upper())
    if code is None:
        return None, f"phone without country code and source country unknown: {raw!r}"
    national = re.sub(r"\D", "", digits).lstrip("0")
    return ("+" + code + national, None) if 6 <= len(national) <= 12 else (None, f"phone not parseable: {raw!r}")


def normalize_url(value: Any) -> tuple[str | None, str | None, str | None]:
    """(url, registrable-ish domain, issue)."""
    if not isinstance(value, str) or not value.strip():
        return None, None, None
    raw = value.strip()
    parts = urlsplit(raw if "://" in raw else "https://" + raw)
    host = (parts.hostname or "").lower()
    if "." not in host:
        return None, None, f"website not parseable: {raw!r}"
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/")
    return f"https://{host}{path}", host, None


def normalize_coordinates(lat: Any, lon: Any) -> tuple[float | None, float | None, str | None]:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None, None if lat is None and lon is None else f"coordinates not numeric: {lat!r},{lon!r}"
    if not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None, None, f"coordinates out of range: {la},{lo}"
    if abs(la) < 1e-9 and abs(lo) < 1e-9:
        return None, None, "coordinates are 0,0 (missing value in source)"
    return round(la, 6), round(lo, 6), None


def normalize_price_range(value: Any) -> tuple[int | None, str | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, (int, float)) and 1 <= int(value) <= 4:
        return int(value), None
    if isinstance(value, str):
        s = value.strip()
        if s and set(s) <= set("$€£¥") and len(s) <= 4:
            return len(s), None
        if s.isdigit() and 1 <= int(s) <= 4:
            return int(s), None
    return None, f"price range not understood: {value!r}"


def _time_ok(t: str) -> bool:
    m = _TIME.match(t)
    return bool(m) and not (m.group(1) == "24" and m.group(2) != "00")


def parse_osm_hours(text: Any) -> tuple[dict[str, Any] | None, str | None]:
    """OSM-style opening_hours subset -> {"weekly": {...}}.

    "Mo-Fr 09:00-18:00; Sa 10:00-14:00; Su off", "24/7",
    "Mo-Su 12:00-15:00,18:00-23:00". Unspecified days are closed (OSM rule).
    Anything else is reported, not guessed."""
    if not isinstance(text, str) or not text.strip():
        return None, None
    raw = text.strip()
    if raw == "24/7":
        return {"weekly": {d: [["00:00", "00:00"]] for d in _DAY_KEYS}}, None
    weekly: dict[str, list[list[str]]] = {d: [] for d in _DAY_KEYS}
    for rule in (r.strip() for r in raw.split(";") if r.strip()):
        m = re.match(r"^([A-Za-z,\- ]+?)\s+(off|closed|[\d:,\- ]+)$", rule)
        if not m:
            return None, f"opening hours not understood: {raw!r}"
        days: list[int] = []
        for part in m.group(1).replace(" ", "").split(","):
            ends = part.lower().split("-")
            if any(e not in _DAYS for e in ends) or len(ends) > 2:
                return None, f"opening hours not understood: {raw!r}"
            a, b = _DAYS.index(ends[0]), _DAYS.index(ends[-1])
            days += list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
        spans: list[list[str]] = []
        if m.group(2) not in ("off", "closed"):
            for span in m.group(2).replace(" ", "").split(","):
                se = span.split("-")
                if len(se) != 2 or not all(_time_ok(x) for x in se):
                    return None, f"opening hours not understood: {raw!r}"
                spans.append(["00:00" if se[0] == "24:00" else se[0], "00:00" if se[1] == "24:00" else se[1]])
        for d in days:
            weekly[_DAY_KEYS[d]] = spans
    return {"weekly": weekly}, None


def _utc(value: Any, tz: str | None) -> tuple[datetime | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None, f"date not understood: {value!r}"
    if dt.tzinfo is None:
        if not tz:
            return None, f"date without timezone and source timezone unknown: {value!r}"
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc), None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# --------------------------------------------------------- format: v1
def _from_v1_place(rec: SourceRecord, d: SourceDescriptor) -> NormalizedEntity:
    p = rec.payload
    prov = p.get("provenance") or {}
    ent = NormalizedEntity("PLACE", rec.record_id, slug_hint=p.get("slug"), region_hint=d.config.get("region"),
                           confidence=prov.get("confidence"))
    record_date = _parse_dt(prov.get("last_verified_at"))
    name = clean_name(p.get("name"))
    if name:
        ent.fields["name"] = name
        ent.aliases.append((name, None, "name"))
    for key in ("subcategory", "address", "timezone", "service_area_km"):
        if p.get(key) not in (None, ""):
            ent.fields[key] = p[key]
    if p.get("description"):
        ent.fields["description"] = p["description"]
    if p.get("tags"):
        ent.fields["tags"] = list(p["tags"])
    lat, lon, issue = normalize_coordinates(p.get("latitude"), p.get("longitude"))
    if issue:
        ent.issues.append(issue)
    if lat is not None:
        ent.latitude, ent.longitude = lat, lon
        ent.fields["coordinates"] = {"lat": lat, "lon": lon}
    phone, issue = normalize_phone(p.get("phone"), d.config.get("country_code"))
    if issue:
        ent.issues.append(issue)
    if phone:
        ent.fields["phone"] = phone
        ent.identifiers.append(("phone", phone))
    url, domain, issue = normalize_url(p.get("website"))
    if issue:
        ent.issues.append(issue)
    if url:
        ent.fields["website"] = url
        if domain not in PLATFORM_DOMAINS:
            ent.identifiers.append(("domain", domain))
    if p.get("price_range") is not None:
        pr, issue = normalize_price_range(p.get("price_range"))
        if pr:
            ent.fields["price_range"] = pr
    for k, v in (p.get("attributes") or {}).items():
        ent.fields[f"attributes.{k}"] = v
    hours = p.get("hours") or {}
    opening = {k: hours[k] for k in ("weekly", "seasonal", "special", "last_entry") if k in hours}
    if opening:
        ent.fields["opening_hours"] = opening
    if "kitchen" in hours:
        ent.fields["kitchen_hours"] = hours["kitchen"]
    if "closed" in hours:
        ent.fields["temporary_closure"] = hours["closed"]
    ver = p.get("verification") or {}
    for f in ent.fields:
        ent.observed[f] = record_date
    for fact, f in (("hours", "opening_hours"), ("kitchen", "kitchen_hours"), ("closure", "temporary_closure"),
                    ("prices", "price_range")):
        if f in ent.fields and ver.get(fact):
            ent.observed[f] = _parse_dt(ver[fact]) or record_date
    ent.meta = {"provider_owned": bool(prov.get("provider_owned")), "is_synthetic": bool(prov.get("is_synthetic")),
                "source_label": prov.get("label")}
    return ent


def _from_v1_event(rec: SourceRecord, d: SourceDescriptor) -> NormalizedEntity:
    p = rec.payload
    prov = p.get("provenance") or {}
    ent = NormalizedEntity("EVENT", rec.record_id, slug_hint=p.get("slug"), region_hint=d.config.get("region"),
                           confidence=prov.get("confidence"), venue_ref=p.get("venue_slug"))
    record_date = _parse_dt(prov.get("last_verified_at"))
    title = p.get("title") or {}
    if title:
        ent.fields["title"] = title
        for lang, t in title.items():
            ent.aliases.append((t, lang, "name"))
    ent.fields["event_category"] = p.get("category") or "other"
    start, issue = _utc(p.get("start"), "UTC")
    if issue:
        ent.issues.append(issue)
    if start:
        ent.fields["start_at"] = _iso(start)
    end, _ = _utc(p.get("end"), "UTC")
    if end:
        ent.fields["end_at"] = _iso(end)
    lat, lon, _ = normalize_coordinates(p.get("latitude"), p.get("longitude"))
    if lat is not None:
        ent.latitude, ent.longitude = lat, lon
        ent.fields["coordinates"] = {"lat": lat, "lon": lon}
    for key in ("description", "ticket_required", "ticket_price", "currency", "age_limit", "language",
                "booking_source"):
        if p.get(key) not in (None, "", {}):
            ent.fields[key] = p[key]
    if p.get("tags"):
        ent.fields["tags"] = list(p["tags"])
    for k, v in (p.get("attributes") or {}).items():
        ent.fields[f"attributes.{k}"] = v
    for f in ent.fields:
        ent.observed[f] = record_date
    ent.meta = {"is_synthetic": bool(prov.get("is_synthetic")), "source_label": prov.get("label")}
    return ent


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------- format: generic
def _from_generic_place(rec: SourceRecord, d: SourceDescriptor) -> NormalizedEntity:
    p = rec.payload
    cfg = d.config
    ent = NormalizedEntity("PLACE", rec.record_id, region_hint=cfg.get("region"), confidence=p.get("confidence"))
    name = clean_name(p.get("name"))
    if name:
        ent.fields["name"] = name
        ent.aliases.append((name, p.get("lang"), "name"))
    for lang, alt in (p.get("names") or {}).items():
        alt = clean_name(alt)
        if alt and alt != name:
            ent.aliases.append((alt, lang, "local" if lang == cfg.get("local_language") else "alternate"))
    loc = p.get("location") or {}
    lat, lon, issue = normalize_coordinates(p.get("lat", loc.get("lat")), p.get("lon", loc.get("lng")))
    if issue:
        ent.issues.append(issue)
    if lat is not None:
        ent.latitude, ent.longitude = lat, lon
        ent.fields["coordinates"] = {"lat": lat, "lon": lon}
    if p.get("address"):
        ent.fields["address"] = clean_name(p["address"])
    phone, issue = normalize_phone(p.get("phone"), cfg.get("country_code"))
    if issue:
        ent.issues.append(issue)
    if phone:
        ent.fields["phone"] = phone
        ent.identifiers.append(("phone", phone))
    url, domain, issue = normalize_url(p.get("website"))
    if issue:
        ent.issues.append(issue)
    if url:
        ent.fields["website"] = url
        if domain not in PLATFORM_DOMAINS:
            ent.identifiers.append(("domain", domain))
    cat = p.get("category")
    if cat is not None:
        from app.places.taxonomy import SUBCATEGORIES

        mapped = (cfg.get("category_map") or {}).get(cat) or (cat if cat in SUBCATEGORIES else None)
        if mapped:
            ent.fields["subcategory"] = mapped
        else:
            ent.issues.append(f"category not mapped: {cat!r}")
            ent.fields["subcategory"] = "unclassified"
    hours, issue = parse_osm_hours(p.get("opening_hours"))
    if issue:
        ent.issues.append(issue)
    if hours:
        ent.fields["opening_hours"] = hours
    kitchen, issue = parse_osm_hours(p.get("kitchen_hours"))
    if issue:
        ent.issues.append(issue)
    if kitchen:
        ent.fields["kitchen_hours"] = kitchen["weekly"]
    if "temporary_closure" in p:
        closure = p["temporary_closure"]
        ent.fields["temporary_closure"] = [closure] if isinstance(closure, dict) else list(closure or [])
    if "permanently_closed" in p:
        ent.fields["permanently_closed"] = bool(p["permanently_closed"])
    pr, issue = normalize_price_range(p.get("price"))
    if issue:
        ent.issues.append(issue)
    if pr:
        ent.fields["price_range"] = pr
    if p.get("cuisine"):
        cuisine = p["cuisine"]
        items = cuisine.split(";") if isinstance(cuisine, str) else list(cuisine)
        ent.fields["attributes.cuisine"] = sorted({fold(c.strip()) for c in items if c and c.strip()})
    for k, v in (p.get("attributes") or {}).items():
        ent.fields[f"attributes.{k}"] = v
    if p.get("timezone"):
        ent.fields["timezone"] = p["timezone"]
    for ns, value in (p.get("ids") or {}).items():
        ent.identifiers.append((f"ext:{ns}", str(value)))
    for ref in p.get("same_as") or []:
        ent.identifiers.append((f"ext:{ref['source']}", str(ref["id"])))
    ent.identifiers.append((f"ext:{d.source_id}", rec.record_id))
    observed = _parse_dt(p.get("updated_at")) or rec.observed_at
    for f in ent.fields:
        ent.observed[f] = observed
    for f, when in (p.get("verified") or {}).items():       # per-field verification dates
        if f in ent.fields and _parse_dt(when):
            ent.observed[f] = _parse_dt(when)
    return ent


def _from_generic_event(rec: SourceRecord, d: SourceDescriptor) -> NormalizedEntity:
    p = rec.payload
    cfg = d.config
    ent = NormalizedEntity("EVENT", rec.record_id, region_hint=cfg.get("region"), confidence=p.get("confidence"),
                           venue_ref=str(p["venue_id"]) if p.get("venue_id") else None)
    titles = {k: clean_name(v) for k, v in (p.get("titles") or {}).items() if clean_name(v)}
    if p.get("title"):
        titles.setdefault(p.get("lang") or "en", clean_name(p["title"]))
    if titles:
        ent.fields["title"] = titles
        for lang, t in titles.items():
            ent.aliases.append((t, lang, "name"))
    ent.fields["event_category"] = p.get("category") or "other"
    tz = p.get("timezone") or cfg.get("timezone")
    start, issue = _utc(p.get("start"), tz)
    if issue:
        ent.issues.append(issue)
    if start:
        ent.fields["start_at"] = _iso(start)
    end, issue = _utc(p.get("end"), tz)
    if end:
        ent.fields["end_at"] = _iso(end)
    venue = p.get("venue") or {}
    lat, lon, _ = normalize_coordinates(p.get("lat", venue.get("lat")), p.get("lon", venue.get("lon")))
    if lat is not None:
        ent.latitude, ent.longitude = lat, lon
        ent.fields["coordinates"] = {"lat": lat, "lon": lon}
    if p.get("organizer"):
        ent.fields["attributes.organizer"] = clean_name(p["organizer"])
        ent.meta["organizer"] = fold(p["organizer"])
    if p.get("performers"):
        ent.fields["attributes.performers"] = sorted(p["performers"])
    if "ticket_required" in p:
        ent.fields["ticket_required"] = bool(p["ticket_required"])
    if p.get("price") is not None:
        ent.fields["ticket_price"] = str(p["price"])
        ent.fields["ticket_required"] = ent.fields.get("ticket_required", True)
    if p.get("currency"):
        ent.fields["currency"] = str(p["currency"]).upper()[:3]
    if p.get("availability"):
        ent.fields["event_availability"] = p["availability"]
    url, domain, _ = normalize_url(p.get("ticket_url"))
    if url:
        ent.fields["booking_source"] = url
        ent.identifiers.append(("ticket_url", url))
    for ns, value in (p.get("ids") or {}).items():
        ent.identifiers.append((f"ext:{ns}", str(value)))
    ent.identifiers.append((f"ext:{d.source_id}", rec.record_id))
    observed = _parse_dt(p.get("updated_at")) or rec.observed_at
    for f in ent.fields:
        ent.observed[f] = observed
    return ent


def normalize(rec: SourceRecord, d: SourceDescriptor) -> NormalizedEntity:
    if rec.kind == "taxonomy":
        return NormalizedEntity("TAXONOMY", rec.record_id, fields=dict(rec.payload))
    if d.format == "hotelbot.world.v1":
        return _from_v1_event(rec, d) if rec.kind == "event" else _from_v1_place(rec, d)
    if d.format == "generic.v1":
        return _from_generic_event(rec, d) if rec.kind == "event" else _from_generic_place(rec, d)
    raise ValueError(f"no normalizer for format {d.format!r}")
