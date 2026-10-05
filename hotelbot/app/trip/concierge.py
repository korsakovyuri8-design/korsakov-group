"""Local concierge: discovery, plan commands and multi-part trip requests.

Sits next to the transaction dialogue and keeps the four states apart:

* DISCOVERY    - "where can I...", "open now", "somewhere lively" ->
                 candidates from structured region data only (never a
                 model's memory), with hours/distance computed, not guessed.
* RESERVATION  - "book 2" on a result -> the transaction dialogue starts a
                 provider reservation for that venue (quote + consent).
* TRANSACTION / CONFIRMATION - owned by TransactionDialogue and stored state.

A multi-part message ("We arrive Friday at 8pm, four of us. Get us a
transfer... dinner still serving when we arrive... skis Saturday morning...")
is split into independent plan items; every item keeps its own status and
the guest confirms only the items they choose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agent import messages as msg
from app.agent.intents import Intent
from app.clock import Clock, as_utc
from app.db.models import Event, ItemStatus, Offering, Place
from app.discovery.engine import Candidate, EventCandidate, discover_events, discover_places
from app.discovery.nlu import DiscoveryRequest, parse_discovery
from app.discovery.render import L, candidate_line, event_line
from app.observability import log_event
from app.places.geo import Point
from app.places.taxonomy import FAMILIES, SUBCATEGORIES, label
from app.schemas.messages import ActionTaken
from app.text import contains_phrase, fold
from app.transactions.catalog import SERVICE_CATALOG, VENUE_CATEGORIES, detect_services
from app.transactions.slots import PROPERTY, local_now, parse_count, parse_when
from app.trip import itinerary

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn
    from app.transactions.dialogue import PlannedOffer, TransactionDialogue

_SAFETY = {Intent.EMERGENCY, Intent.HUMAN_REQUEST, Intent.COMPLAINT}

_SAVE = r"save|keep|remember|sacuvaj|zapamti|сохрани|запомни"
_BOOK = r"book|reserve|rezervisi|rezervisite|забронируй|забронируйте|бронируй"
_COMMAND = re.compile(rf"^\s*(?P<verb>{_SAVE}|{_BOOK})\s+(?:#|no\.?\s*|number\s+|broj\s+|номер\s+)?"
                      rf"(?P<n>\d{{1,2}})\b(?P<rest>.*)$")

PLAN_PHRASES = ["my plan", "our plan", "itinerary", "what am i doing", "what are we doing", "what's planned",
                "what is planned", "whats planned", "what do i have", "what do we have", "my schedule",
                "moj plan", "nas plan", "sta imam", "sta imamo", "sta je u planu", "raspored",
                "мой план", "наш план", "что у меня", "что у нас", "что запланировано", "расписание"]
ARRIVAL = ["we arrive", "i arrive", "arriving", "we land", "landing", "our flight lands", "we get in",
           "stizemo", "dolazimo", "stizem", "dolazim", "slijecemo",
           "прилетаем", "приезжаем", "прибываем", "прилетаю", "приезжаю"]
WHEN_WE_ARRIVE = ["when we arrive", "when we get there", "when we get in", "when we land", "on arrival",
                  "after we arrive", "kad stignemo", "kada stignemo", "kad mi stignemo", "po dolasku",
                  "когда приедем", "когда мы приедем", "когда прилетим", "когда мы прилетим", "по приезду",
                  "когда доберемся", "когда мы доберемся"]
WEEKDAYS = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "cnr": ["pon", "uto", "sri", "čet", "pet", "sub", "ned"],
            "ru": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]}
# Explicit "outside the property" cues. Without one, a question the property's
# own knowledge pack answers ("Where is the parking?") stays with the pack.
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
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")

SECTION = {
    "dinner": {"en": "Dinner", "cnr": "Večera", "ru": "Ужин"},
    "drinks": {"en": "Drinks", "cnr": "Piće", "ru": "Выпить"},
    "events": {"en": "Events", "cnr": "Događaji", "ru": "События"},
}
_TEXT = {
    "eta": {"en": " (estimated arrival at the hotel ~{t}, from the provider's typical journey time)",
            "cnr": " (procijenjeni dolazak u hotel ~{t}, prema uobičajenom trajanju vožnje)",
            "ru": " (ориентировочное прибытие в отель ~{t}, по обычному времени в пути)"},
    "serving_at": {"en": "kitchen open at {t}", "cnr": "kuhinja radi u {t}", "ru": "кухня работает в {t}"},
    "open_at": {"en": "open at {t}", "cnr": "otvoreno u {t}", "ru": "открыто в {t}"},
    "need": {"en": "{label}: {question}", "cnr": "{label}: {question}", "ru": "{label}: {question}"},
    "unsupported": {"en": "{label}: I can't arrange this through a provider here - I can pass it to the staff.",
                    "cnr": "{label}: ovo ne mogu da organizujem preko partnera - mogu proslijediti osoblju.",
                    "ru": "{label}: это я не могу организовать через партнёра — могу передать сотрудникам."},
    "failed": {"en": "{label}: the provider did not answer - staff will follow up. Nothing is booked.",
               "cnr": "{label}: partner nije odgovorio - osoblje će se javiti. Ništa nije rezervisano.",
               "ru": "{label}: партнёр не ответил — сотрудники свяжутся с вами. Ничего не забронировано."},
    "nothing_found": {"en": "{label}: nothing in my local data matches{ctx}.",
                      "cnr": "{label}: ništa u mojim lokalnim podacima ne odgovara{ctx}.",
                      "ru": "{label}: в моих местных данных ничего подходящего{ctx}."},
    "health": {"en": "If it is urgent, call {n}. I can't give medical advice.",
               "cnr": "Ako je hitno, pozovite {n}. Ne mogu davati medicinske savjete.",
               "ru": "Если это срочно, звоните {n}. Медицинских советов я не даю."},
    "health_no_number": {"en": "If it is urgent, call the local emergency number. I can't give medical advice.",
                         "cnr": "Ako je hitno, pozovite lokalni broj za hitne slučajeve. Ne mogu davati medicinske savjete.",
                         "ru": "Если это срочно, звоните по местному экстренному номеру. Медицинских советов я не даю."},
    "synthetic": {"en": "(Demo data - synthetic places.)", "cnr": "(Demo podaci - izmišljena mjesta.)",
                  "ru": "(Демо-данные — вымышленные места.)"},
}


def day_label(day: date | datetime, locale: str) -> str:
    names = WEEKDAYS.get(locale.split("-")[0], WEEKDAYS["en"])
    name = names[day.weekday()]
    if locale == "cnr-Cyrl":
        name = msg.to_cyrillic_template(name)
    return f"{name} {day.strftime('%d.%m')}"


def T(key: str, locale: str, **kw: object) -> str:
    entry = _TEXT[key]
    text = msg.to_cyrillic_template(entry["cnr"]) if locale == "cnr-Cyrl" else (
        entry.get(locale.split("-")[0]) or entry["en"])
    return text.format(**kw) if kw else text


def _loc(table: dict[str, str], locale: str) -> str:
    if locale == "cnr-Cyrl":
        return msg.to_cyrillic_template(table["cnr"])
    return table.get(locale.split("-")[0]) or table["en"]


def _hits(text: str, phrases: list[str]) -> bool:
    folded = fold(text)
    return any(contains_phrase(folded, p) for p in phrases)


@dataclass
class _Section:
    kind: str                              # transaction | discovery
    text: str
    service_type: str | None = None
    request: DiscoveryRequest | None = None
    planned: PlannedOffer | None = None
    results: list[Candidate] = field(default_factory=list)
    events: list[EventCandidate] = field(default_factory=list)
    at: datetime | None = None


class Concierge:
    def __init__(self, clock: Clock, transactions: TransactionDialogue | None) -> None:
        self.clock = clock
        self.txn = transactions

    # ------------------------------------------------------------ helpers
    def _now_local(self, turn: _Turn) -> datetime:
        return local_now(self.clock.now(), turn.runtime.timezone)

    def _tz(self, turn: _Turn) -> ZoneInfo:
        return ZoneInfo(turn.runtime.timezone)

    def _fmt(self, turn: _Turn, dt: datetime | None, *, with_time: bool = True) -> str:
        if dt is None:
            return ""
        if dt.tzinfo is not None:
            dt = dt.astimezone(self._tz(turn))
        return day_label(dt, turn.language) + (dt.strftime(" %H:%M") if with_time else "")

    def _party(self, turn: _Turn, text: str) -> int | None:
        return parse_count(fold(text)) or turn.stay.party_size or \
            (turn.stay.facts or {}).get("guest_count", {}).get("value")

    # ========================================================== commands
    def handle_command(self, turn: _Turn) -> bool:
        """"save 2" / "book 1" on the last results, and "my plan"."""
        if turn.intent is not None and turn.intent.intent in _SAFETY:
            return False
        m = _COMMAND.match(fold(turn.text))
        results = turn.state.get("last_results") or []
        if m and results:
            n = int(m.group("n"))
            if not 1 <= n <= len(results):
                return False
            entry = results[n - 1]
            if re.fullmatch(_SAVE, m.group("verb")):
                return self._save(turn, entry)
            return self._book(turn, entry)
        if _hits(turn.text, PLAN_PHRASES) and turn.intent is not None and \
                turn.intent.intent not in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST):
            return self._show_plan(turn)
        return False

    def _entry_target(self, turn: _Turn, entry: dict[str, Any]) -> tuple[Place | None, Event | None]:
        if entry.get("event_id"):
            event = turn.session.get(Event, entry["event_id"])
            return (turn.session.get(Place, event.place_id) if event and event.place_id else None), event
        return turn.session.get(Place, entry.get("place_id")), None

    def _save(self, turn: _Turn, entry: dict[str, Any]) -> bool:
        place, event = self._entry_target(turn, entry)
        if place is None and event is None:
            return False
        title = (event.title.get(turn.language.split("-")[0]) or event.title.get("en")) if event else place.name
        starts = as_utc(event.start_at) if event else (
            datetime.fromisoformat(entry["at"]).replace(tzinfo=self._tz(turn)) if entry.get("at") else None)
        item = itinerary.add(turn.session, stay_id=turn.stay.id, kind=(event.category if event else place.category),
                             title=title, status=ItemStatus.SAVED, starts_at=starts,
                             place_id=place.id if place else None, event_id=event.id if event else None)
        turn.actions.append(ActionTaken(kind="plan_item", id=item.id, detail={"status": "saved", "title": title}))
        turn.reply, turn.succeeded = msg.t("saved", turn.language, title=title), True
        return True

    def _book(self, turn: _Turn, entry: dict[str, Any]) -> bool:
        place, event = self._entry_target(turn, entry)
        if place is None:
            return False
        offering = turn.session.scalar(select(Offering).where(
            Offering.place_id == place.id, Offering.active,
            Offering.service_type.in_(list(VENUE_CATEGORIES))))
        if event is None and offering is not None and offering.provider_id and self.txn is not None \
                and turn.runtime.capabilities.service(offering.service_type) is not None:
            preset: dict[str, Any] = {}
            if entry.get("at"):
                preset["reservation_time"] = datetime.fromisoformat(entry["at"]).replace(
                    tzinfo=self._tz(turn)).isoformat()
            if entry.get("party"):
                preset["party_size"] = entry["party"]
            return self.txn.try_start_service(turn, offering.service_type, preset=preset, venue=place)
        walkin = msg.t("walk_in_ok", turn.language) if (place.attributes or {}).get("walk_in_supported") else ""
        turn.reply = msg.t("not_bookable", turn.language, title=place.name, walkin=walkin)
        turn.succeeded = True
        return True

    def _show_plan(self, turn: _Turn) -> bool:
        now_local = self._now_local(turn)
        parts = parse_when(fold(turn.text), now_local.date())
        day = parts.day
        entries = itinerary.plan(turn.session, turn.stay.id)
        if day is not None:
            entries = [e for e in entries if e.starts_at and e.starts_at.astimezone(self._tz(turn)).date() == day]
        ctx = f" ({day_label(day, turn.language)})" if day else ""
        if not entries:
            turn.reply, turn.succeeded = msg.t("plan_empty", turn.language, ctx=ctx), True
            return True
        lines = [msg.t("plan_header", turn.language, ctx=ctx)]
        for e in entries:
            when = self._fmt(turn, e.starts_at)
            lines.append(f"- {when + ' · ' if when else ''}{e.item.title} - {msg.status_label(e.status, turn.language)}")
        turn.reply, turn.succeeded = "\n".join(lines), True
        return True

    # ========================================================= discovery
    def handle_discovery(self, turn: _Turn) -> bool:
        if not turn.runtime.region or turn.intent is None:
            return False
        if turn.intent.intent in _SAFETY or turn.intent.intent in (Intent.REQUEST_STATUS, Intent.BOOKING_REQUEST):
            return False
        now_local = self._now_local(turn)
        req = parse_discovery(turn.text, now_local)
        if req is None:
            return False
        if not _hits(turn.text, EXTERNAL) and self._property_answers(turn, req):
            return False   # property knowledge first ("Where is the parking?")
        req.query.party_size = self._party(turn, turn.text)
        section = _Section("discovery", turn.text, request=req)
        self._run_discovery(turn, section, now_local)
        lines: list[str] = []
        results_state: list[dict[str, Any]] = []
        self._render_discovery(turn, section, lines, results_state, header=True)
        if not section.results and not section.events:
            turn.reply = L("none", turn.language, ctx=self._ctx(turn, section))
            if turn.runtime.capabilities.can("staff_question"):
                turn.set_state(pending_offer={"kind": "ask_staff", "question": turn.text})
            turn.succeeded = True
            return True
        lines.append(self._footer(turn, section.results))
        if self._synthetic(section):
            lines.append(T("synthetic", turn.language))
        turn.reply = "\n".join(lines)
        turn.set_state(last_results=results_state)
        turn.grounded, turn.succeeded = True, True
        turn.sources = [f"place:{c.place.slug}" for c in section.results] + \
            [f"event:{e.event.slug}" for e in section.events]
        log_event("discovery", conversation_id=turn.conv.id, hits=req.hits, events=req.events,
                  results=[c.place.slug for c in section.results], essential=req.essential)
        return True

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
    def _synthetic(section: _Section) -> bool:
        return any(c.place.is_synthetic for c in section.results) or any(e.event.is_synthetic for e in section.events)

    def _run_discovery(self, turn: _Turn, section: _Section, now_local: datetime) -> None:
        req = section.request
        assert req is not None
        q = req.query
        q.region = turn.runtime.region or ""
        if turn.runtime.location:
            q.near = Point(*turn.runtime.location)
        if section.at is not None:
            q.at = section.at.replace(tzinfo=None)
            if not q.serving_at and q.open_until is None:
                q.open_at = True
        if req.essential and q.max_km is None:
            q.limit = 3
        now_utc = self.clock.now()
        if q.subcategories or q.categories:
            section.results = discover_places(turn.session, q, now_utc, turn.runtime.timezone)
        if req.events:
            tz = self._tz(turn)
            if q.at is not None:
                start = datetime.combine(q.at.date(), time(0, 0), tzinfo=tz)
                end = start + timedelta(days=1)
            else:
                start, end = now_utc, now_utc + timedelta(days=7)
            tags = {t for t in q.tags_preferred if t in ("jazz",)} or None
            section.events = discover_events(turn.session, q.region, now_utc, start, end, tags=tags, limit=3)

    def _ctx(self, turn: _Turn, section: _Section) -> str:
        q = section.request.query if section.request else None
        if q is None or q.at is None:
            return ""
        return L("ctx_at", turn.language, t=self._fmt(turn, q.at))

    def _render_discovery(self, turn: _Turn, section: _Section, lines: list[str],
                          results_state: list[dict[str, Any]], *, header: bool) -> None:
        req = section.request
        assert req is not None
        q = req.query
        ctx = self._ctx(turn, section)
        if header and section.results:
            if req.essential:
                what = ", ".join(dict.fromkeys(label(s, turn.language) for s in sorted(q.subcategories)))
                lines.append(L("header_essential", turn.language, what=what, ctx=ctx))
            else:
                lines.append(L("header", turn.language, ctx=ctx))
        at_iso = q.at.isoformat() if q.at else None
        for c in section.results:
            n = len(results_state) + 1
            line = candidate_line(n, c, turn.language)
            if q.at is not None and q.serving_at:
                line += "; " + T("serving_at", turn.language, t=q.at.strftime("%H:%M"))
            lines.append(line)
            results_state.append({"place_id": c.place.id, "at": at_iso, "party": q.party_size})
        if section.events:
            lines.append(L("events_header", turn.language, ctx=ctx))
            tz = self._tz(turn)
            for e in section.events:
                n = len(results_state) + 1
                lines.append(event_line(n, e, turn.language, lambda dt: self._fmt(turn, as_utc(dt).astimezone(tz))))
                results_state.append({"event_id": e.event.id, "at": None, "party": q.party_size})
        cats = {c.place.category for c in section.results} | (
            {SUBCATEGORIES[s].category.value for s in q.subcategories if s in SUBCATEGORIES})
        if "HEALTH" in cats:
            number = turn.runtime.emergency_number
            lines.append(T("health", turn.language, n=number) if number else T("health_no_number", turn.language))

    def _footer(self, turn: _Turn, results: list[Candidate]) -> str:
        bookable = any(self._bookable(turn, c.place) for c in results)
        return L("footer", turn.language, book=L("footer_book", turn.language) if bookable else "")

    def _bookable(self, turn: _Turn, place: Place) -> bool:
        offering = turn.session.scalar(select(Offering).where(
            Offering.place_id == place.id, Offering.active, Offering.service_type.in_(list(VENUE_CATEGORIES))))
        return offering is not None and offering.provider_id is not None and \
            turn.runtime.capabilities.service(offering.service_type) is not None

    # ======================================================= trip planner
    def handle_plan(self, turn: _Turn) -> bool:
        """A message with several independent requests -> independent plan items."""
        if self.txn is None or turn.intent is None or turn.intent.intent in _SAFETY:
            return False
        segments = [s.strip() for s in _SENTENCE.split(turn.text) if s.strip()]
        if len(segments) < 2:
            return False
        now_local = self._now_local(turn)
        sections: list[_Section] = []
        arrival: datetime | None = None
        for seg in segments:
            services = [s for s in detect_services(seg) if s not in VENUE_CATEGORIES]
            disc = parse_discovery(seg, now_local, require_cue=False) if turn.runtime.region else None
            if services:
                sections.append(_Section("transaction", seg, service_type=services[0]))
            elif disc is not None:
                sections.append(_Section("discovery", seg, request=disc))
            if _hits(seg, ARRIVAL) and arrival is None:
                parts = parse_when(fold(seg), now_local.date())
                if parts.day and parts.time:
                    arrival = datetime.combine(parts.day, parts.time, tzinfo=self._tz(turn))
        if len(sections) < 2:
            return False
        party = self._party(turn, turn.text)
        log_event("trip_plan", conversation_id=turn.conv.id, items=[s.service_type or s.request.hits
                                                                    for s in sections])
        eta = arrival
        for sec in sections:
            if sec.kind != "transaction":
                continue
            preset, inferred = self._preset(sec, arrival, party)
            sec.planned = self.txn.prepare(turn, sec.service_type, sec.text, preset=preset, inferred=inferred)
            if sec.service_type in ("airport_transfer", "taxi") and sec.planned.outcome == "offered" and arrival:
                minutes = (sec.planned.provider.config or {}).get("estimated_duration_minutes")
                if minutes:
                    eta = arrival + timedelta(minutes=int(minutes))
                    sec.at = eta
        for sec in sections:
            if sec.kind != "discovery":
                continue
            assert sec.request is not None
            sec.request.query.party_size = party
            if _hits(sec.text, WHEN_WE_ARRIVE) and eta is not None:
                sec.at = eta
                sec.request.time_explicit = True
            self._run_discovery(turn, sec, now_local)
        return self._compose(turn, sections)

    @staticmethod
    def _preset(sec: _Section, arrival: datetime | None, party: int | None) -> tuple[dict[str, Any], list[str]]:
        preset: dict[str, Any] = {}
        inferred: list[str] = []
        spec = SERVICE_CATALOG[sec.service_type or ""]
        keys = {f.key: f.kind for f in spec.fields}
        if party and "party_size" in keys:
            preset["party_size"] = party
        if sec.service_type in ("airport_transfer", "taxi") and arrival is not None:
            when_key = next((k for k, kind in keys.items() if kind == "datetime"), None)
            if when_key and not parse_when(fold(sec.text), arrival.date()).time:
                preset[when_key] = arrival.isoformat()
                inferred.append(when_key)
            if "destination" in keys:
                preset["destination"] = PROPERTY
                inferred.append("destination")
        return preset, inferred

    def _section_label(self, turn: _Turn, sec: _Section) -> str:
        from app.actions.catalog import action_label

        if sec.service_type:
            return action_label(sec.service_type, turn.language)
        q = sec.request.query if sec.request else None
        if q is not None:
            if q.subcategories & FAMILIES.get("restaurant", set()):
                return _loc(SECTION["dinner"], turn.language)
            if "NIGHTLIFE" in q.categories or q.subcategories & FAMILIES.get("bar", set()):
                return _loc(SECTION["drinks"], turn.language)
            if q.subcategories:
                return ", ".join(dict.fromkeys(label(s, turn.language) for s in sorted(q.subcategories)))
        return _loc(SECTION["events"], turn.language)

    def _compose(self, turn: _Turn, sections: list[_Section]) -> bool:
        assert self.txn is not None
        lines = [msg.t("trip_header", turn.language)]
        results_state: list[dict[str, Any]] = []
        codes: list[str] = []
        draft_set = False
        any_results = []
        for sec in sections:
            title = self._section_label(turn, sec)
            if sec.kind == "transaction":
                p = sec.planned
                assert p is not None
                if p.outcome == "offered" and p.quote is not None:
                    line = f"• {title}: {self.txn.offer_line(turn, p.quote, with_label=False)}"
                    if sec.at is not None and sec.service_type in ("airport_transfer", "taxi"):
                        line += T("eta", turn.language, t=sec.at.strftime("%H:%M"))
                    if p.quote.conditions:
                        line += f". {p.quote.conditions}"
                    lines.append(line)
                    codes.append(p.quote.code)
                elif p.outcome == "missing" and p.draft is not None:
                    lines.append("• " + T("need", turn.language, label=title, question=self.txn.question(turn, p.draft)))
                    if not draft_set:
                        turn.set_state(txn_draft=p.draft)
                        draft_set = True
                elif p.outcome == "unavailable":
                    lines.append(f"• {p.detail}")
                elif p.outcome == "failed":
                    lines.append("• " + T("failed", turn.language, label=title))
                else:
                    lines.append("• " + T("unsupported", turn.language, label=title))
                continue
            q = sec.request.query if sec.request else None
            ctx = self._ctx(turn, sec)
            if not sec.results and not sec.events:
                lines.append("• " + T("nothing_found", turn.language, label=title, ctx=ctx))
                continue
            lines.append(f"• {title}{ctx}:")
            self._render_discovery(turn, sec, lines, results_state, header=False)
            any_results += sec.results
            if sec.results:
                first = sec.results[0].place
                names = ", ".join(c.place.name for c in sec.results)
                itinerary.add(turn.session, stay_id=turn.stay.id, kind=first.category, title=f"{title}: {names}",
                              status=ItemStatus.SHORTLISTED,
                              starts_at=q.at.replace(tzinfo=self._tz(turn)) if q and q.at else None,
                              details={"options": [c.place.id for c in sec.results]})
        if codes:
            lines.append(msg.t("trip_footer", turn.language, example=codes[0]))
        if results_state:
            lines.append(self._footer(turn, any_results))
            turn.set_state(last_results=results_state)
        if any(s.results and self._synthetic(s) for s in sections):
            lines.append(T("synthetic", turn.language))
        turn.reply = "\n".join(lines)
        turn.succeeded = True
        turn.grounded = True if results_state else turn.grounded
        return True
