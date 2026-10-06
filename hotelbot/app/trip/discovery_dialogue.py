"""Discovery dialogue: traveller preferences, single and compound discovery
requests, free windows, rendering of results (with conflicts, freshness,
attribution) and no-result explanations."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.agent.intents import Intent
from app.clock import as_utc
from app.db.models import Place
from app.discovery.engine import DiscoveryQuery, discover_events, run
from app.discovery.nlu import DiscoveryRequest, parse_discovery, understand
from app.discovery.render import L, candidate_line, event_line, rejection_summary
from app.marketplace import bridge
from app.observability import log_event
from app.places.taxonomy import SUBCATEGORIES, label
from app.shared.geo import Point
from app.text import fold
from app.transactions.catalog import VENUE_CATEGORIES
from app.trip import itinerary, preferences, schedule

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn
from app.trip.compose import (T, _PREF_LABELS, _SAFETY, _hits, _Section,
                               day_label)


EXTERNAL = ["nearby", "near", "in town", "around here", "close by", "closest", "nearest", "open now", "still open",
            "recommend*", "suggest*", "find", "somewhere", "any good", "local", "in the area", "u blizini", "u gradu",
            "najbliz*", "preporu*", "negdje", "nadji", "pronadji", "otvoreno sada", "рядом", "поблизости", "в городе",
            "ближайш*", "посоветуй*", "порекоменд*", "найди*", "где-нибудь", "местн*"]
# Property knowledge (key / category) that answers the same kind of need as a
# discovery category.
_PROPERTY_TOPICS = [
    ({"restaurant", "breakfast", "dining"}, "FOOD"),
    ({"bar", "dining"}, "NIGHTLIFE"),
    ({"parking", "transport", "airport_transfer"}, "MOBILITY_INFRASTRUCTURE"),
    ({"airport_transfer", "transport"}, "TRANSPORT"),
    ({"spa", "wellness", "amenities"}, "WELLNESS"),
    ({"wifi"}, "CONNECTIVITY"),
    ({"ski_storage"}, "RENTAL"),
    ({"local_durmitor", "local_activities"}, "ATTRACTION"),
    ({"local_durmitor", "local_activities"}, "ACTIVITY"),
    ({"payment_methods", "billing"}, "FINANCIAL_SERVICE"),
]


class DiscoveryDialogue:
    # ======================================================= preferences
    def _capture_preferences(self, turn: _Turn) -> list[preferences.Stated]:
        stated = preferences.extract(turn.text)
        if stated:
            preferences.save(turn.session, turn.stay.guest_id, stated, turn.guest_message_id)
            log_event("traveler_preference_saved", conversation_id=turn.conv.id, keys=[s.key for s in stated])
        return stated

    def _pref_text(self, turn: _Turn, keys: dict[str, Any]) -> str:
        out = []
        for k, v in keys.items():
            if k in _PREF_LABELS:
                out.append(L(_PREF_LABELS[k], turn.language))
            elif k == "noise_level":
                out.append(L(f"tag:{v}", turn.language) if f"tag:{v}" in ("tag:lively", "tag:quiet") else str(v))
            elif k == "local":
                out.append(L("tag:local", turn.language))
            elif k == "price":
                out.append({"en": "budget-friendly", "cnr": "povoljno", "ru": "недорого"}.get(
                    turn.language.split("-")[0], "budget-friendly"))
            else:
                out.append(k.replace("_", " "))
        return ", ".join(out)

    # ========================================================= discovery
    def handle_discovery(self, turn: _Turn) -> bool:
        if not turn.runtime.region or turn.intent is None:
            return False
        if turn.intent.intent in _SAFETY or turn.intent.intent in (Intent.REQUEST_STATUS, Intent.BOOKING_REQUEST):
            return False
        now_local = self._now_local(turn)
        stated = self._capture_preferences(turn)
        if schedule.is_free_window_request(turn.text):
            if self._free_window(turn, now_local):
                return True
        reqs = [r for r in understand(turn.text, now_local) if self._allowed(turn, r)]
        if not reqs:
            if stated:
                turn.reply = T("pref_noted", turn.language,
                               what=self._pref_text(turn, {s.key: s.value for s in stated}))
                turn.succeeded = True
                self._outcome(turn, "SAVE")
                return True
            return False
        if len(reqs) == 1 and not _hits(turn.text, EXTERNAL) and self._property_answers(turn, reqs[0]):
            return False   # property knowledge first ("Where is the parking?")
        sections = []
        for req in reqs:
            if req.query.party_size is None:
                req.query.party_size = self._party(turn, turn.text)
            sec = _Section("discovery", req.text, request=req)
            self._run_discovery(turn, sec, now_local)
            sections.append(sec)
        lines: list[str] = []
        results_state: list[dict[str, Any]] = []
        if len(sections) == 1:
            sec = sections[0]
            if not sec.results and not sec.events:
                self._no_results(turn, sec)
                return True
            self._render_discovery(turn, sec, lines, results_state, header=True)
        else:
            for sec in sections:
                title = self._section_label(turn, sec)
                if not sec.results and not sec.events:
                    lines.append("• " + self._nothing_line(turn, sec, title))
                    continue
                lines.append(f"• {title}{self._ctx(turn, sec)}{self._anchor_note(turn, sec)}:")
                self._render_discovery(turn, sec, lines, results_state, header=False)
        if not results_state:
            turn.reply, turn.succeeded = "\n".join(lines), True
            self._outcome(turn, "FIND")
            return True
        used = self._used_preferences(sections)
        if used:
            lines.append(T("pref_used", turn.language, what=self._pref_text(turn, used)))
        lines.append(self._footer(turn, sections))
        if any(self._synthetic(s) for s in sections):
            lines.append(T("synthetic", turn.language))
        lines += self._attribution(turn, sections)
        turn.reply = "\n".join(lines)
        turn.set_state(last_results=results_state)
        turn.grounded, turn.succeeded = True, True
        turn.sources = [f"place:{c.place.slug}" for s in sections for c in s.results] + \
            [f"event:{e.event.slug}" for s in sections for e in s.events]
        self._outcome(turn, "RECOMMEND" if any(s.request and s.request.recommend for s in sections) else "FIND")
        log_event("discovery", conversation_id=turn.conv.id, requests=[s.request.hits for s in sections if s.request],
                  results=[c.place.slug for s in sections for c in s.results],
                  rejected={k: v for s in sections if s.result for k, v in s.result.rejected.items()})
        return True

    def _no_results(self, turn: _Turn, sec: _Section) -> None:
        why = rejection_summary(sec.result.rejected, turn.language) if sec.result else ""
        ctx = self._ctx(turn, sec)
        turn.reply = L("none_because", turn.language, ctx=ctx, why=why) if why else L("none", turn.language, ctx=ctx)
        if turn.runtime.capabilities.can("staff_question"):
            turn.set_state(pending_offer={"kind": "ask_staff", "question": turn.text})
        turn.succeeded = True
        self._outcome(turn, "FIND")

    def _nothing_line(self, turn: _Turn, sec: _Section, title: str) -> str:
        why = rejection_summary(sec.result.rejected, turn.language) if sec.result else ""
        return T("nothing_found", turn.language, label=title, ctx=self._ctx(turn, sec), why=f" ({why})" if why else "")

    @staticmethod
    def _used_preferences(sections: list[_Section]) -> dict[str, Any]:
        used: dict[str, Any] = {}
        for s in sections:
            if s.request is None:
                continue
            for c in s.results:
                for k, v in s.request.query.traveller.items():
                    if (c.place.attributes or {}).get(k) == v:
                        used[k] = v
        return used

    def _free_window(self, turn: _Turn, now_local: datetime) -> bool:
        entries = itinerary.plan(turn.session, turn.stay.id)
        window = schedule.free_window(turn.text, entries, now_local, turn.runtime.timezone)
        if window is None:
            return False
        need_text = schedule.strip_window_words(turn.text)    # "before dinner" is WHEN, not WHAT
        req = parse_discovery(need_text, now_local, require_cue=False)
        if req is None or not (req.query.categories or req.query.subcategories):
            req = req or DiscoveryRequest(query=DiscoveryQuery(region=""), text=turn.text)
            req.query.categories = {"ATTRACTION", "CULTURE", "NATURE", "WELLNESS"}
            req.query.subcategories = {"cafe"}
            req.label = "sights"
        req.events = False
        if not self._allowed(turn, req):
            return False
        q = req.query
        q.at, q.window_end, q.visit_minutes, q.open_at = window.start, window.end, 1, True
        q.open_until = None
        sec = _Section("discovery", turn.text, request=req)
        self._run_discovery(turn, sec, now_local)
        why = T("window_before", turn.language, t=window.anchor) if window.anchor else ""
        if not window.from_plan and re.search(r"before|prije|do |перед|до ", fold(turn.text)):
            why = T("window_no_plan", turn.language, what=fold(turn.text).split("before")[-1].strip(" ?.!") or "it")
        self._outcome(turn, "FIND")
        if not sec.results:
            self._no_results(turn, sec)
            return True
        lines = [T("window", turn.language, a=window.start.strftime("%H:%M"), b=window.end.strftime("%H:%M"), why=why)]
        results_state: list[dict[str, Any]] = []
        self._render_discovery(turn, sec, lines, results_state, header=False)
        lines.append(self._footer(turn, [sec]))
        if self._synthetic(sec):
            lines.append(T("synthetic", turn.language))
        lines += self._attribution(turn, [sec])
        turn.reply = "\n".join(lines)
        turn.set_state(last_results=results_state)
        turn.grounded, turn.succeeded = True, True
        turn.sources = [f"place:{c.place.slug}" for c in sec.results]
        return True

    @staticmethod
    def _allowed(turn: _Turn, req: DiscoveryRequest) -> bool:
        """Discovery capabilities: drop categories this property does not
        expose; nothing left = not a discovery turn here."""
        caps = turn.runtime.capabilities
        if not caps.can_discover():
            return False
        q = req.query
        q.categories = {c for c in q.categories if caps.can_discover(c)}
        q.subcategories = {s for s in q.subcategories
                           if s in SUBCATEGORIES and caps.can_discover(SUBCATEGORIES[s].category.value)}
        if req.events and not caps.can_discover_events():
            req.events = False
        return bool(q.categories or q.subcategories or req.events)

    @staticmethod
    def _property_answers(turn: _Turn, req: DiscoveryRequest) -> bool:
        """The property's own pack has a grounded answer about the same kind
        of thing (its parking, its restaurant) - not merely a keyword overlap
        ("after midnight" vs. the late-arrival policy)."""
        retrieval = turn.runtime.knowledge.search(turn.text, turn.language, k=1)
        if not retrieval.grounded:
            return False
        item = retrieval.items[0]
        cats = set(req.query.categories) | {SUBCATEGORIES[s].category.value for s in req.query.subcategories
                                            if s in SUBCATEGORIES}
        related = {cat for words, cat in _PROPERTY_TOPICS if {item.key, item.category, item.topic} & words}
        return bool(related & cats) or bool({item.key, item.topic} & req.query.subcategories)

    @staticmethod
    def _attribution(turn: _Turn, sections: list[_Section]) -> list[str]:
        """Sources whose licence requires attribution, when their data is shown."""
        from app.world.attribution import lines_for

        ids: set[str] = set()
        for sec in sections:
            for c in sec.results:
                ids |= set((c.place.resolution or {}).get("_attribution", []))
            for e in sec.events:
                ids |= set((e.event.resolution or {}).get("_attribution", []))
        return lines_for(turn.session, ids, turn.language)

    @staticmethod
    def _synthetic(section: _Section) -> bool:
        return any(c.place.is_synthetic for c in section.results) or any(e.event.is_synthetic for e in section.events)

    def _resolve_anchor(self, turn: _Turn, text: str) -> tuple[Point, str] | None:
        """"near the Black Lake" -> that place's coordinates, from the region
        data only. Unknown anchor -> None (the caller says so; no guessing)."""
        target = fold(text)
        rows = turn.session.scalars(select(Place).where(Place.region == turn.runtime.region, Place.active))
        best = None
        for p in rows:
            name = fold(p.name)
            if p.latitude is None:
                continue
            if target in name or all(w in name for w in target.split() if len(w) > 2):
                if best is None or len(p.name) < len(best.name):
                    best = p
        return (Point(best.latitude, best.longitude), best.name) if best else None

    def _anchor_for_activity(self, turn: _Turn, subcategory: str) -> tuple[Point, str] | None:
        p = turn.session.scalar(select(Place).where(Place.region == turn.runtime.region, Place.active,
                                                    Place.subcategory == subcategory).order_by(Place.slug).limit(1))
        return (Point(p.latitude, p.longitude), p.name) if p is not None and p.latitude is not None else None

    def _run_discovery(self, turn: _Turn, section: _Section, now_local: datetime) -> None:
        req = section.request
        assert req is not None
        q = req.query
        q.region = turn.runtime.region or ""
        if turn.runtime.location:
            q.near = Point(*turn.runtime.location)
        anchor = None
        if req.anchor_text:
            anchor = self._resolve_anchor(turn, req.anchor_text)
        anchor = anchor or section.anchor
        if anchor is not None:
            q.near, q.anchor_label = anchor
            section.anchor = anchor
            if q.max_km == 2.0:
                q.max_km = None     # "near the lake": nearest to the anchor, not 2 km from the hotel
        if section.at is not None:
            q.at = section.at.replace(tzinfo=None)
            if not q.serving_at and q.open_until is None:
                q.open_at = True
        if req.essential and q.max_km is None:
            q.limit = 3
        stored = preferences.load(turn.session, turn.stay.guest_id)
        if stored:
            attrs, price, tags = preferences.as_ranking(stored)
            q.traveller = {k: v for k, v in attrs.items() if k not in q.required and k not in q.preferred}
            q.price_pref = q.price_pref or price
            q.tags_preferred |= tags
        now_utc = self.clock.now()
        if q.subcategories or q.categories:
            signals = bridge.ranking_signals(turn.session, property_id=turn.runtime.property_id,
                                             capabilities=turn.runtime.capabilities)
            section.result = run(turn.session, q, now_utc, turn.runtime.timezone, signals=signals)
            section.results = section.result.candidates
        if req.events:
            tz = self._tz(turn)
            if req.event_window is not None:
                start = req.event_window[0].replace(tzinfo=tz)
                end = req.event_window[1].replace(tzinfo=tz)
            elif q.at is not None:
                start = datetime.combine(q.at.date(), time(0, 0), tzinfo=tz)
                end = start + timedelta(days=1)
            else:
                start, end = now_utc, now_utc + timedelta(days=7)
            tags = {t for t in q.relevance_tags if t in ("jazz",)} or None
            section.events = discover_events(turn.session, q.region, now_utc, start, end, tags=tags,
                                             categories=req.event_categories or None, limit=3, min_age=q.min_age,
                                             near=q.near)

    def _ctx(self, turn: _Turn, section: _Section) -> str:
        req = section.request
        q = req.query if req else None
        if req is not None and req.essential and q is not None and (q.at is None or not req.time_explicit):
            return ""
        if req is not None and req.events and not (q and (q.categories or q.subcategories)) and req.event_window:
            start, end = req.event_window
            evening = start.hour >= 17
            return L("ctx_at", turn.language, t=day_label(start, turn.language) +
                     (T("evening", turn.language) if evening else ""))
        if q is None or q.at is None:
            return ""
        return L("ctx_at", turn.language, t=self._fmt(turn, q.at))

    def _anchor_note(self, turn: _Turn, section: _Section) -> str:
        if section.anchor is None:
            return ""
        return T("near_anchor", turn.language, a=section.anchor[1])

    def _render_discovery(self, turn: _Turn, section: _Section, lines: list[str],
                          results_state: list[dict[str, Any]], *, header: bool) -> None:
        req = section.request
        assert req is not None
        q = req.query
        ctx = self._ctx(turn, section)
        anchor_name = q.anchor_label or {"en": "the hotel", "cnr": "hotela", "ru": "отеля"}.get(
            turn.language.split("-")[0], "the hotel")
        if header and section.results:
            if req.essential:
                what = ", ".join(dict.fromkeys(label(s, turn.language) for s in sorted(q.subcategories)))
                lines.append(L("header_essential", turn.language, what=what, ctx=ctx))
            else:
                lines.append(L("header", turn.language, ctx=ctx + self._anchor_note(turn, section)))
        at_iso = q.at.isoformat() if q.at else None
        section_label = self._section_label(turn, section)
        from app.places.hours import OpenState

        for c in section.results:
            n = len(results_state) + 1
            line = candidate_line(n, c, turn.language, anchor=anchor_name, now=q.at is None)
            if q.at is not None and q.serving_at and c.kitchen is not None and c.kitchen.state == OpenState.OPEN:
                line += "; " + T("serving_at", turn.language, t=q.at.strftime("%H:%M"))
            lines.append(line)
            results_state.append({"kind": "place", "place_id": c.place.id, "category": c.place.category,
                                  "subcategory": c.place.subcategory, "name": c.place.name, "at": at_iso,
                                  "party": q.party_size, "section": section_label})
        if section.events:
            if section.results or header:
                lines.append(L("events_header", turn.language, ctx=ctx))
            tz = self._tz(turn)
            for e in section.events:
                n = len(results_state) + 1
                lines.append(event_line(n, e, turn.language, lambda dt: self._fmt(turn, as_utc(dt).astimezone(tz))))
                results_state.append({"kind": "event", "event_id": e.event.id, "event_category": e.event.category,
                                      "name": e.event.title.get("en") or e.event.slug,
                                      "at": as_utc(e.event.start_at).astimezone(tz).replace(tzinfo=None).isoformat(),
                                      "party": q.party_size, "section": "events"})
        cats = {c.place.category for c in section.results} | (
            {SUBCATEGORIES[s].category.value for s in q.subcategories if s in SUBCATEGORIES})
        if "HEALTH" in cats:
            number = turn.runtime.emergency_number
            lines.append(T("health", turn.language, n=number) if number else T("health_no_number", turn.language))

    def _footer(self, turn: _Turn, sections: list[_Section]) -> str:
        bookable = any(self._bookable(turn, c.place) for s in sections for c in s.results)
        return L("footer", turn.language, book=L("footer_book", turn.language) if bookable else "")

    def _bookable(self, turn: _Turn, place: Place) -> bool:
        return any(o.service_type in VENUE_CATEGORIES
                   for o in bridge.options_for(turn.session, place=place, capabilities=turn.runtime.capabilities))
