"""How a place is named to a traveller - one rule for every guest-facing path
(discovery lines, event venues, saved / planned items and their replies).

An OSM element id used as a fallback name (node/123, way/456, relation/789)
is an internal identifier, never a name: the localised kind is shown instead,
plus the source's address when it has one. Nothing is invented, and the
stored Place.name is not changed."""

from __future__ import annotations

import re

from app.places.taxonomy import label

_TECHNICAL_NAME = re.compile(r"(node|way|relation)/\d+")


def is_unnamed(place) -> bool:  # noqa: ANN001
    return not place.name or _TECHNICAL_NAME.fullmatch(place.name.strip()) is not None


def display_name(place, locale: str) -> str:  # noqa: ANN001
    if not is_unnamed(place):
        return place.name
    kind = label(place.subcategory, locale)
    kind = kind[:1].upper() + kind[1:]
    address = getattr(place, "address", None)
    return f"{kind}, {address}" if address else kind
