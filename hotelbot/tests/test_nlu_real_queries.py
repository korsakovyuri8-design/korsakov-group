"""Iteration 6.1: discovery NLU defects found on the frozen Kotor/Budva world
(docs/ITERATION_6_1.md §3), each fixed on its own: "interesting",
"in <town>", "late"."""

from __future__ import annotations

from datetime import datetime

from app.discovery.nlu import parse_discovery

LOCAL = datetime(2026, 10, 8, 11, 17)
SIGHTS = {"ATTRACTION", "CULTURE", "NATURE"}


def _p(text: str, require_cue: bool = True):
    return parse_discovery(text, LOCAL, require_cue=require_cue)


# ------------------------------------------------------------ "interesting"
def test_something_interesting_near_the_old_town_is_a_sightseeing_request():
    for require_cue in (True, False):
        req = _p("Something interesting near the Old Town", require_cue)
        assert req is not None and req.label == "sights" and req.query.categories == SIGHTS
        assert req.anchor_text == "old town"                    # near the anchor, not the whole region
        assert not req.query.subcategories and not req.query.open_at


def test_interesting_with_a_cue_in_other_languages():
    assert _p("Anything interesting to see in town?").query.categories == SIGHTS
    assert _p("Nešto zanimljivo u blizini?").query.categories == SIGHTS
    assert _p("Что-нибудь интересное рядом?").query.categories == SIGHTS


def test_interesting_alone_is_not_a_discovery_request():
    for text in ("That's interesting", "Interesting, thanks!", "What an interesting idea"):
        assert _p(text) is None and _p(text, require_cue=False) is None, text


def test_interesting_happening_stays_an_events_request():
    req = _p("Anything interesting happening tonight?")
    assert req.events and not req.query.categories
