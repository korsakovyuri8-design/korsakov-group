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
