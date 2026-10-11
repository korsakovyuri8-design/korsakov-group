"""Iteration 6.1: which OSM tag decides the category (docs/ITERATION_6_1.md §2).

Tags are walked in the existing key order (amenity, tourism, shop, historic,
...). The first tag the source's category_map knows wins - but a generic
mapped tag (tourism=attraction, historic=yes) never beats a specific mapped
one. Only when no tag is mapped does the record become "unclassified"."""

from __future__ import annotations

from app.world.adapters import SourceDescriptor, SourceRecord
from app.world.normalize import normalize

MAP = {"historic=memorial": "monument", "natural=beach": "beach", "historic=ruins": "historic_site",
       "tourism=attraction": "tourist_attraction", "historic=yes": "historic_site", "leisure=park": "park",
       "amenity=place_of_worship": "religious_site", "amenity=bar": "bar", "leisure=beach_resort": "beach"}


def _sub(tags: dict[str, str], cmap: dict[str, str] = MAP) -> tuple[str, list[str]]:
    cfg = {"category_map": cmap, "country_code": "ME", "timezone": "Europe/Podgorica", "region": "r",
           "local_language": "sr"}
    d = SourceDescriptor("osm", "api", "directory", "osm", "osm.overpass.v1", "ODbL-1.0", config=cfg)
    ent = normalize(SourceRecord("node/1", "place", {"type": "node", "id": 1, "lat": 42.42, "lon": 18.77,
                                                     "tags": {"name": "X", **tags}}), d)
    return ent.fields["subcategory"], ent.issues


def test_incidental_unmapped_tag_does_not_hide_a_mapped_one():
    assert _sub({"amenity": "drinking_water", "historic": "memorial"})[0] == "monument"
    assert _sub({"tourism": "yes", "natural": "beach"})[0] == "beach"


def test_specific_mapped_tag_beats_generic_mapped_tag():
    assert _sub({"tourism": "attraction", "historic": "ruins"})[0] == "historic_site"
    assert _sub({"historic": "yes", "leisure": "park"})[0] == "park"


def test_generic_mapped_tag_is_used_when_alone():
    assert _sub({"tourism": "attraction"})[0] == "tourist_attraction"
    assert _sub({"tourism": "attraction", "natural": "tree"})[0] == "tourist_attraction"


def test_key_order_still_decides_between_specific_mapped_tags():
    assert _sub({"amenity": "place_of_worship", "historic": "ruins"})[0] == "religious_site"
    assert _sub({"amenity": "bar", "leisure": "beach_resort"})[0] == "bar"


def test_nothing_mapped_stays_unclassified_and_names_the_first_tag():
    sub, issues = _sub({"amenity": "drinking_water", "natural": "tree"})
    assert sub == "unclassified" and "category not mapped: 'amenity=drinking_water'" in issues
