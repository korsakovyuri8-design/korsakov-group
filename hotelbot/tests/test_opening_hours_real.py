"""Iteration 6.1: opening_hours syntax found in the frozen Kotor/Budva raw_v1
(docs/ITERATION_6_1.md). Classes A (no day selector), B1 (PH merged into an
all-days selector) and C (", " additional rule) are parsed. HH:MM is normalised.
Anything the weekly model cannot state exactly stays unparsed, i.e. UNKNOWN."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.places.hours import OpenState, status_at
from app.world.normalize import parse_osm_hours

ALL_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def weekly(text: str) -> dict:
    hours, issue = parse_osm_hours(text)
    assert issue is None, issue
    return hours["weekly"]


# --------------------------------------------------- A: no day selector
@pytest.mark.parametrize("text, span", [
    ("09:00-22:00", ["09:00", "22:00"]),
    ("07:00-01:00", ["07:00", "01:00"]),
    ("7:00-24:00", ["07:00", "00:00"]),
])
def test_bare_time_span_applies_every_day(text, span):
    assert all(weekly(text)[d] == [span] for d in ALL_DAYS)


def test_bare_time_list_applies_every_day():
    assert weekly("12:00-15:00,18:00-23:00")["sun"] == [["12:00", "15:00"], ["18:00", "23:00"]]


def test_overnight_bare_span_runs_past_midnight():
    hours = {"weekly": weekly("07:00-01:00")}
    late = status_at(hours, datetime(2026, 10, 13, 0, 30))        # Tue 00:30, still Monday's range
    assert late.state == OpenState.OPEN and late.closes_at == datetime(2026, 10, 13, 1, 0)
    assert status_at(hours, datetime(2026, 10, 13, 3, 0)).state == OpenState.OPEN_LATER


# --------------------------------------------------- HH:MM everywhere
def test_times_are_normalised_to_hh_mm():
    assert weekly("Mo-Su 7:00-9:30")["wed"] == [["07:00", "09:30"]]
    assert weekly("Fr-Sa 18:00-02:00")["sat"] == [["18:00", "02:00"]]


# --------------------------------------------------- B1: PH + every day
@pytest.mark.parametrize("text, span", [
    ("Mo-Su,PH 09:00-22:00", ["09:00", "22:00"]),
    ("PH,Mo-Su 07:00-23:00", ["07:00", "23:00"]),
])
def test_public_holidays_merged_into_every_day_are_exact(text, span):
    assert all(weekly(text)[d] == [span] for d in ALL_DAYS)


# --------------------------------------------------- C: ", " additional rule
def test_comma_separated_additional_rules():
    w = weekly("Mo-Fr 08:00-20:00, Sa 08:00-15:00")
    assert w["mon"] == w["fri"] == [["08:00", "20:00"]]
    assert w["sat"] == [["08:00", "15:00"]] and w["sun"] == []


def test_additional_rule_overnight():
    w = weekly("Mo-Fr 07:00-01:00, Sa,Su 07:00-02:00")
    assert w["fri"] == [["07:00", "01:00"]] and w["sun"] == [["07:00", "02:00"]]


def test_additional_rule_adds_to_a_day_already_named():
    # OSM: an additional rule does not override - Sunday gets the union.
    w = weekly("Tu-Th,Su 14:00-24:00, Fr-Su 14:00-01:00")
    assert w["sun"] == [["14:00", "01:00"]] and w["tue"] == [["14:00", "00:00"]]
    assert w["fri"] == [["14:00", "01:00"]] and w["mon"] == []
    sun_late = status_at({"weekly": w}, datetime(2026, 10, 18, 23, 30))
    assert sun_late.closes_at == datetime(2026, 10, 19, 1, 0)


def test_semicolon_still_overrides():
    assert weekly("Mo-Su 10:00-22:00; Su off")["sun"] == []


# --------------------------------------------------- regression: stays UNKNOWN
UNSUPPORTED = [
    # B2 - a rule of its own for public holidays (needs a holiday calendar)
    "Mo-Fr 08:00-18:00; Sa,Su,PH 09:00-18:00",
    "Mo-Sa 07:30-23:00; Su off; PH off",
    "Mo-Sa 07:00-21:00; PH closed",
    "Mo-Sa 07:30-21:00; PH unknown",
    "Mo-Sa 07:30-21:30; Su,PH off",
    "Mo-Fr 08:00-20:00, PH off",
    # D - month ranges (not in this fix)
    "07:00-01:00; Dec-Feb 07:00-24:00",
    "Sep-May Mo-Sa 08:00-21:00; Sep-May Su 09:00-16:00; Jun-Aug: Mo-Su 08:00-22:00",
    # E1 - free-text seasons
    "Winter 10:00-24:00; summer 10:00-02:00",
    "08:00-23:00 winter; 08:00-24:00 summer",
    "off season closed",
    # E2 - not a schedule
    "closed",
    '"closed"',
    "off",
]


@pytest.mark.parametrize("text", UNSUPPORTED)
def test_unsupported_syntax_stays_unknown(text):
    hours, issue = parse_osm_hours(text)
    assert hours is None and "not understood" in issue
