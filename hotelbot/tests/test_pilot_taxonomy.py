"""Iteration 6.1: the Kotor/Budva pilot's OSM category_map covers what the
frozen raw_v1 actually contained (docs/ITERATION_6_1.md §2) - explicitly,
value by value; an unseen value still stays unclassified."""

from __future__ import annotations

import yaml

from tests.conftest import ROOT

PILOT = ROOT / "data" / "pilot" / "kotor_budva" / "pilot.yaml"


def _osm_map() -> dict[str, str]:
    cfg = yaml.safe_load(PILOT.read_text())
    return next(s for s in cfg["sources"] if s["source_id"] == "osm")["category_map"]


def test_observed_historic_values_map_explicitly_to_historic_site():
    cmap = _osm_map()
    for value in ("city_gate", "tomb", "yes", "ship", "cannon", "pillory", "building"):
        assert cmap.get(f"historic={value}") == "historic_site", value


def test_no_wildcard_for_unseen_historic_values():
    cmap = _osm_map()
    assert not any(k in cmap for k in ("historic=*", "historic", "historic=wayside_cross"))


# ------------------------------------------- new subcategories (data the pilot requested)
NEW = {"convenience_store": ("SHOPPING", "shop=convenience",
                             {"en": "convenience store", "cnr": "prodavnica", "ru": "магазин у дома"}),
       "tourist_attraction": ("ATTRACTION", "tourism=attraction",
                              {"en": "tourist attraction", "cnr": "turistička atrakcija", "ru": "достопримечательность"}),
       "artwork": ("CULTURE", "tourism=artwork", {"en": "artwork", "cnr": "umjetničko djelo", "ru": "арт-объект"}),
       "boat_rental": ("RENTAL", "amenity=boat_rental",
                       {"en": "boat rental", "cnr": "iznajmljivanje čamaca", "ru": "прокат лодок"})}


def test_new_subcategories_exist_with_labels_and_no_search_keywords():
    from app.places.taxonomy import SUBCATEGORIES

    for key, (cat, _, labels) in NEW.items():
        sub = SUBCATEGORIES[key]
        assert sub.category.value == cat and sub.labels == labels, key
        assert sub.keywords == (), key            # discovery NLU is a separate defect, not changed here


def test_requested_osm_values_map_to_the_new_subcategories():
    cmap = _osm_map()
    for key, (_, tag, _) in NEW.items():
        assert cmap.get(tag) == key, tag


def test_specific_beats_generic_with_the_real_pilot_map():
    from tests.test_osm_category_selection import _sub

    assert _sub({"tourism": "attraction", "historic": "ruins"}, _osm_map())[0] == "historic_site"
    assert _sub({"tourism": "attraction"}, _osm_map())[0] == "tourist_attraction"
    assert _sub({"amenity": "drinking_water", "historic": "memorial"}, _osm_map())[0] == "monument"
