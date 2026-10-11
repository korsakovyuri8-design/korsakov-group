"""Real-source formats (Iteration 6): OpenStreetMap (Overpass JSON) and
Wikidata (SPARQL JSON) -> the generic place shape the generic normalizer
reads. The SOURCE-NATIVE record is what the adapter keeps as raw; these
functions only map fields, they never invent a value:

* a tag that is absent stays absent (no default hours, no guessed phone);
* several phones in one tag ("+382 ...; +382 ...") -> the first is the
  identity phone, the rest stay in the raw record and an issue is noted;
* names: `name` is the primary name; `name:<lang>`, `int_name`, `alt_name`
  are aliases with their language; `old_name` is NOT an alias (a former
  name can belong to the business that replaced it);
* categories map through the source's `category_map` (data, per source):
  the first tag (in OSM_CATEGORY_KEYS order) the map knows wins, a generic
  tag (OSM_GENERIC_TAGS) only when no specific one is mapped; with no mapped
  tag the record becomes "unclassified" plus an issue, as for any source.
"""

from __future__ import annotations

import re
from typing import Any

OSM_CATEGORY_KEYS = ("amenity", "tourism", "shop", "historic", "leisure", "natural", "man_made", "office")


def _first(value: str | None) -> tuple[str | None, bool]:
    """'a; b' -> ('a', True). OSM separates multiple values with ';'."""
    if not value:
        return None, False
    parts = [v.strip() for v in str(value).split(";") if v.strip()]
    return (parts[0] if parts else None), len(parts) > 1


# Tags that say "something of this kind" without saying what: a specific mapped
# tag on the same element always decides instead.
OSM_GENERIC_TAGS = frozenset({"tourism=attraction", "tourism=yes", "historic=yes"})


def osm_categories(tags: dict[str, str]) -> list[str]:
    """Every tag that could say what this is, as 'key=value', in key order."""
    return [f"{key}={tags[key]}" for key in OSM_CATEGORY_KEYS if tags.get(key)]


def osm_category(tags: dict[str, str], known: dict[str, str] | None = None) -> str | None:
    """The tag that decides the category (the category_map key): the first
    mapped specific tag, else the first mapped generic tag, else the first tag."""
    cands = osm_categories(tags)
    mapped = [c for c in cands if known and c in known]
    return next((c for c in mapped if c not in OSM_GENERIC_TAGS), mapped[0] if mapped else
                (cands[0] if cands else None))


def osm_address(tags: dict[str, str]) -> str | None:
    street, number = tags.get("addr:street"), tags.get("addr:housenumber")
    place = tags.get("addr:city") or tags.get("addr:place") or tags.get("addr:suburb")
    head = " ".join(x for x in (street or tags.get("addr:place"), number) if x)
    if not head:
        return None
    return f"{head}, {place}" if place and place != head else head


def osm_to_generic(element: dict[str, Any]) -> dict[str, Any]:
    tags = element.get("tags") or {}
    rid = f"{element['type']}/{element['id']}"
    lat = element.get("lat", (element.get("center") or {}).get("lat"))
    lon = element.get("lon", (element.get("center") or {}).get("lon"))
    phone, many_phones = _first(tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile"))
    website, _ = _first(tags.get("website") or tags.get("contact:website") or tags.get("url"))
    names = {}
    for key, value in tags.items():
        m = re.fullmatch(r"name:([a-zA-Z\-]+)", key)
        if m:
            names[m.group(1)] = value
    if tags.get("int_name"):
        names.setdefault("int", tags["int_name"])
    for i, alt in enumerate(_split(tags.get("alt_name"))):
        names[f"alt{i}" if i else "alt"] = alt
    out: dict[str, Any] = {
        "id": rid,
        "type": "place",
        "name": tags.get("name"),
        "names": names,
        "lat": lat,
        "lon": lon,
        "category": osm_category(tags),
        "ids": {"osm": rid, **({"wikidata": tags["wikidata"]} if tags.get("wikidata") else {})},
    }
    if osm_address(tags):
        out["address"] = osm_address(tags)
    if phone:
        out["phone"] = phone
    if website:
        out["website"] = website
    if tags.get("opening_hours"):
        out["opening_hours"] = tags["opening_hours"]
    if tags.get("opening_hours:kitchen"):
        out["kitchen_hours"] = tags["opening_hours:kitchen"]
    if tags.get("cuisine"):
        out["cuisine"] = tags["cuisine"]
    if element.get("timestamp"):
        out["updated_at"] = element["timestamp"]          # last edit of the element, not a field check
    checked = tags.get("check_date:opening_hours") or tags.get("check_date")
    if checked and out.get("opening_hours"):
        out["verified"] = {"opening_hours": checked}
    attributes = {k: tags[k] for k in ("wheelchair", "outdoor_seating", "internet_access", "diet:vegan",
                                       "diet:vegetarian", "takeaway", "delivery") if tags.get(k)}
    if attributes:
        out["attributes"] = attributes
    if many_phones:
        out["_issues"] = ["several phones in one tag; the first is used for identity"]
    return out


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in str(value).split(";") if v.strip()] if value else []


# ------------------------------------------------------------------ Wikidata
def _point(wkt: str | None) -> tuple[float | None, float | None]:
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", wkt or "")
    return (float(m.group(2)), float(m.group(1))) if m else (None, None)


def wikidata_group(bindings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """SPARQL rows -> rows per item (one item spans several rows: one per
    label language, type, website ...)."""
    items: dict[str, list[dict[str, Any]]] = {}
    for row in bindings:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        items.setdefault(qid, []).append(row)
    return items


def wikidata_to_generic(qid: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    def values(var: str) -> list[str]:
        return list(dict.fromkeys(r[var]["value"] for r in rows if var in r))

    labels: dict[str, str] = {}
    for r in rows:
        if "label" in r:
            labels.setdefault(r["label"].get("xml:lang", "und"), r["label"]["value"])
    primary = labels.get("sr-el") or labels.get("sh") or labels.get("hr") or labels.get("en") or \
        next(iter(labels.values()), None)
    lat, lon = _point(next(iter(values("coord")), None))
    types = sorted(v.rsplit("/", 1)[-1] for v in values("type"))
    out: dict[str, Any] = {"id": qid, "type": "place", "name": primary,
                           "names": {lang: v for lang, v in labels.items() if v != primary},
                           "lat": lat, "lon": lon,
                           # the first type that the source's category_map knows is chosen by the normalizer
                           "category": types[0] if types else None, "types": types,
                           "ids": {"wikidata": qid}}
    osm_node = next(iter(values("osmNode")), None)
    osm_way = next(iter(values("osmWay")), None)
    osm_rel = next(iter(values("osmRel")), None)
    if osm_node:
        out["ids"]["osm"] = f"node/{osm_node}"
    elif osm_way:
        out["ids"]["osm"] = f"way/{osm_way}"
    elif osm_rel:
        out["ids"]["osm"] = f"relation/{osm_rel}"
    website = next(iter(values("website")), None)
    if website:
        out["website"] = website
    phone = next(iter(values("phone")), None)
    if phone:
        out["phone"] = phone
    address = next(iter(values("address")), None)
    if address:
        out["address"] = address
    return out
