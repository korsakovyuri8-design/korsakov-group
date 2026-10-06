"""Trip planner: a multi-part message -> statements (context) and
independent requests (transactions, discovery), with arrival / day /
activity context, then one composed plan reply."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any


from app.agent import messages as msg
from app.db.models import ExternalProvider, ItemStatus, Quote
from app.discovery.nlu import understand
from app.observability import log_event
from app.places.taxonomy import FAMILIES, label
from app.text import contains_phrase, fold
from app.transactions.catalog import SERVICE_CATALOG, VENUE_CATEGORIES, detect_services, keyword_positions, rental_label
from app.transactions.slots import PROPERTY, parse_places, parse_when
from app.trip import itinerary

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn
from app.trip.compose import (SECTION, T, _SAFETY, _hits, _loc, _Section)
from app.trip.discovery_dialogue import EXTERNAL  # noqa: F401


ARRIVAL = ["we arrive", "i arrive", "arriving", "we land", "landing", "our flight lands", "we get in",
           "stizemo", "dolazimo", "stizem", "dolazim", "slijecemo",
           "прилетаем", "приезжаем", "прибываем", "прилетаю", "приезжаю"]
WHEN_WE_ARRIVE = ["when we arrive", "when we get there", "when we get in", "when we land", "on arrival",
                  "after we arrive", "kad stignemo", "kada stignemo", "kad mi stignemo", "po dolasku",
                  "когда приедем", "когда мы приедем", "когда прилетим", "когда мы прилетим", "по приезду",
                  "когда доберемся", "когда мы доберемся"]
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")

# Statements of fact about the trip: context, never a new order.
_STATEMENT = [
    re.compile(r"\balready (?:booked|reserved|arranged|have|got|sorted|paid|organi[sz]ed)\b"),
    re.compile(r"\b(?:is|are|was|were|has been|have been) (?:already )?(?:booked|reserved|arranged|sorted|confirmed)\b"),
    re.compile(r"^\W*(?:[a-z]+\s+){0,2}(?:we|i)(?:'re| are| am|'m| will be|'ll be) [a-z]+ing\b"),
    re.compile(r"^\W*(?:[a-z]+\s+){0,2}(?:we|i) (?:have|'ve got|have got|got) (?:the|our|a|an|my)\b"),
    re.compile(r"\bvec (?:smo |sam |je )?(?:rezervis|imamo|imam|dogovor)"),
    re.compile(r"\bimamo (?:vodic|transfer|rezervac)"),
    re.compile(r"\buzhe (?:zabronir|zakaza|est)|\bu nas (?:uzhe |est )?(?:gid|transfer|bron)|\bmy katae"),
]
_REQUEST_CUES = ["find", "book", "get us", "get me", "need", "want", "suggest", "recommend", "can you", "could you",
                 "please", "looking for", "where", "what", "tell me", "is there", "show", "organi*", "arrange",
                 "nadji", "trebam*", "zelim*", "hocemo", "preporu*", "найди*", "нужен", "нужна", "нужно", "хотим",
                 "посоветуй*", "закажи*", "забронируй*"]
_ACTIVITIES = {"ski_resort": ["skiing", "ski", "on the slopes", "skijamo", "skijanje", "skijati", "катаемся на лыжах",
                              "на лыжах", "лыжи", "katamsya na lyzhah"],
               "trailhead": ["hiking", "hike", "trekking", "planinarimo", "в поход*"],
               "lake": ["at the lake", "kod jezera", "на озере"]}

def is_statement(text: str) -> bool:
    """"Our transfer is already booked", "Saturday we're skiing", "Sunday we
    have the guide" - facts about the trip, not requests."""
    folded = fold(text)
    if not any(p.search(folded) for p in _STATEMENT):
        return False
    return not any(contains_phrase(folded, c) for c in _REQUEST_CUES)


_TODAY = ["today", "tonight", "this evening", "this morning", "this afternoon", "danas", "veceras", "jutros",
          "сегодня"]


def explicit_day(text: str, today: date) -> date | None:
    """The day the words NAME. A bare day part ("at night", "in the
    evening") is not a day - it belongs to the day under discussion."""
    parts = parse_when(fold(text), today)
    if parts.day is None:
        return None
    if parts.approximate and parts.day == today and not _hits(text, _TODAY):
        return None
    return parts.day


def _activity(text: str) -> str | None:
    folded = fold(text)
    return next((sub for sub, words in _ACTIVITIES.items() if any(contains_phrase(folded, w) for w in words)), None)


@dataclass
class _TripContext:
    day: date | None = None             # arrival day
    arrival: datetime | None = None     # exact arrival time, if given ("8pm"); not "evening"
    origin: str | None = None           # where they arrive from ("from Podgorica")
    current_day: date | None = None     # the day the conversation is currently about
    activities: dict[date, str] = field(default_factory=dict)   # "Saturday we're skiing" -> ski_resort
    eta: datetime | None = None         # estimated arrival at the property
    eta_note: str | None = None


_DECISION = re.compile(r"^\s*(yes|no|book|confirm|reserve|go ahead|cancel|decline|i'?ll decide|let me think|"
                       r"da\b|ne\b|rezervis|potvrd|otkaz|da\b|net\b|bronir|zabronir|podtver|otmen|"
                       r"да\b|нет\b|брониру|заброниру|подтвер|отмен)")
_DELIMS = re.compile(r"(,\s*(?:and\s+|i\s+|и\s+)?|\s+(?:and|plus|also|as well as|i|kao i|а также|и)\s+)")


def split_requests(text: str) -> list[str]:
    """Sentences, and within a sentence one part per requested service:
    "Get us a transfer, two sets of skis for Saturday, and a guide on
    Sunday." -> three parts. A part keeps the words around its keyword."""
    out: list[str] = []
    for sentence in (x.strip() for x in _SENTENCE.split(text) if x.strip()):
        folded = fold(sentence)
        hits = keyword_positions(folded)
        services = list(dict.fromkeys(svc for _, svc in hits))
        if len(services) < 2:
            out.append(sentence)
            continue
        # fold() lower-cases, strips diacritics and transliterates Cyrillic,
        # so map folded positions back to the original text.
        prefix = [len(fold(sentence[:i])) for i in range(len(sentence) + 1)]

        def original(pos: int) -> int:
            return next((i for i, n in enumerate(prefix) if n >= pos), len(sentence))

        cuts = [0]
        folded_len = len(folded)
        for pos, svc in hits[1:]:
            if svc == services[0] and len(cuts) == 1:
                continue
            delims = [m for m in _DELIMS.finditer(folded, cuts[-1], pos)]
            cut = delims[-1].start() if delims else pos
            if cuts[-1] < cut < folded_len:
                cuts.append(cut)
        cuts = [original(c) for c in cuts] + [len(sentence)]
        parts = [sentence[a:b].strip(" ,") for a, b in zip(cuts, cuts[1:], strict=False)]
        out += [p for p in parts if p]
    return out


class TripPlanner:
    # ======================================================= trip planner
    def handle_plan(self, turn: _Turn) -> bool:
        """A message with several independent requests (and statements of
        trip context) -> independent plan items."""
        if self.txn is None or turn.intent is None or turn.intent.intent in _SAFETY:
            return False
        segments = split_requests(turn.text)
        if len(segments) < 2 or self._answers_open_offers(turn):
            return False
        now_local = self._now_local(turn)
        tz = self._tz(turn)
        sections: list[_Section] = []
        ctx = _TripContext()
        for seg in segments:
            folded = fold(seg)
            parts = parse_when(folded, now_local.date())
            if _hits(seg, ARRIVAL) and ctx.day is None:
                ctx.day = parts.day
                if parts.day and parts.time and not parts.approximate:
                    ctx.arrival = datetime.combine(parts.day, parts.time, tzinfo=tz)
                places, _ = parse_places(seg)
                ctx.origin = places.get("pickup")
            if (named := explicit_day(seg, now_local.date())) is not None:
                ctx.current_day = named
            services = [s for s in detect_services(seg) if s not in VENUE_CATEGORIES]
            if is_statement(seg):
                day = named or (ctx.day if services and SERVICE_CATALOG[services[0]].domain == "transport"
                                    else ctx.current_day)
                if (act := _activity(seg)) and day:
                    ctx.activities[day] = act
                sections.append(_Section("statement", seg, service_type=services[0] if services else None, day=day))
                continue
            if services:
                sections.append(_Section("transaction", seg, service_type=services[0]))
                continue
            if not turn.runtime.region:
                continue
            for req in understand(seg, now_local):
                if not self._allowed(turn, req):
                    continue
                own_day = explicit_day(req.text, now_local.date())
                day = own_day or (None if req.essential else ctx.current_day)
                sections.append(_Section("discovery", req.text, request=req, day=day, own_day=own_day))
        actionable = [s for s in sections if s.kind != "statement"]
        statements = [s for s in sections if s.kind == "statement"]
        # One request on its own stays with its own handler; it comes here
        # only as DISCOVERY that needs the stated trip context ("Saturday
        # we're skiing. Find somewhere casual for lunch.").
        if len(actionable) < 2 and not (statements and any(s.kind == "discovery" for s in actionable)):
            return False
        party = self._party(turn, turn.text)
        log_event("trip_plan", conversation_id=turn.conv.id,
                  items=[s.service_type or (s.request.hits if s.request else s.kind) for s in sections])
        plan_entries = itinerary.plan(turn.session, turn.stay.id)
        eta = ctx.arrival
        for sec in sections:
            if sec.kind == "statement":
                sec.note, found_eta = self._statement_note(turn, sec, plan_entries, ctx)
                eta = found_eta or eta
        for sec in sections:
            if sec.kind != "transaction":
                continue
            preset, partial, inferred = self._preset(sec, ctx, party)
            sec.planned = self.txn.prepare(turn, sec.service_type, sec.text, preset=preset, preset_partial=partial,
                                           inferred=inferred)
            if sec.service_type in ("airport_transfer", "taxi") and sec.planned.outcome == "offered" and ctx.arrival:
                minutes = (sec.planned.provider.config or {}).get("estimated_duration_minutes")
                if minutes:
                    eta = ctx.arrival + timedelta(minutes=int(minutes))
                    sec.at = eta
        for sec in sections:
            if sec.kind != "discovery":
                continue
            assert sec.request is not None
            q = sec.request.query
            q.party_size = q.party_size or party
            if _hits(sec.text, WHEN_WE_ARRIVE) and (eta is not None or ctx.day is not None):
                sec.at = eta or (datetime.combine(ctx.day, time(20, 0), tzinfo=tz) if ctx.day else None)
                sec.request.time_explicit = True
                if not q.serving_at and sec.request.label in ("food", "lunch", None):
                    q.serving_at = True
            elif sec.day is not None and sec.own_day is None and not sec.request.essential:
                self._inherit_day(sec, sec.day, now_local.date())
            day = (q.at.date() if q.at else None) or sec.day
            daytime = q.at is None or q.at.hour < 17          # skiing / hiking happen by day
            if day and day in ctx.activities and daytime and not sec.request.anchor_text \
                    and not sec.request.essential:
                sec.anchor = self._anchor_for_activity(turn, ctx.activities[day])
            self._run_discovery(turn, sec, now_local)
        return self._compose(turn, sections)

    @staticmethod
    def _inherit_day(sec: _Section, day: date, today: date) -> None:
        """"Saturday we're skiing. Find somewhere casual for lunch." -> lunch
        on Saturday; "At night ... cocktails" -> that night."""
        from app.discovery.nlu import _default_time

        req = sec.request
        assert req is not None
        q = req.query
        folded = fold(req.text)
        shift = day - (q.at.date() if q.at is not None else today)
        when = parse_when(folded, day)
        t = when.time if when.time and not when.approximate else None
        if t is None:
            if any(contains_phrase(folded, w) for w in ("at night", "tonight", "night", "late", "noc*", "ночь*",
                                                        "ночью")):
                t = time(22, 0)
            elif any(contains_phrase(folded, w) for w in ("evening", "uvece", "вечер*")):
                t = time(19, 0) if req.label != "drinks" else time(22, 0)
            else:
                t = _default_time(req.label)
        q.at = datetime.combine(day, t)
        if q.open_until is not None:          # "still open after midnight" - that night, not tonight
            q.open_until += shift
        if q.window_end is not None:
            q.window_end += shift
        if req.label in ("lunch", "food", "breakfast"):
            q.serving_at, q.open_at = True, False
        elif q.open_until is None:
            q.open_at = True
        if req.events:
            start_h = 17 if any(contains_phrase(folded, w) for w in ("evening", "tonight", "night", "uvece",
                                                                      "вечер*")) else 0
            req.event_window = (datetime.combine(day, time(0)) + timedelta(hours=start_h),
                                datetime.combine(day, time(0)) + timedelta(hours=24))

    def _statement_note(self, turn: _Turn, sec: _Section, entries: list[itinerary.PlanEntry],
                        ctx: _TripContext) -> tuple[str, datetime | None]:
        """A statement about the trip, checked against the plan. Returns the
        line and, for a booked transfer, the estimated arrival time."""
        text = T("noted", turn.language, text=sec.text.strip().rstrip("."))
        if sec.service_type is None:
            return text, None
        domain = SERVICE_CATALOG[sec.service_type].domain
        tz = self._tz(turn)
        match = None
        for e in entries:
            if e.item.quote_id is None or e.status in ("expired", "cancelled", "rejected", "failed"):
                continue
            quote = turn.session.get(Quote, e.item.quote_id)
            if quote is None or SERVICE_CATALOG.get(quote.service_type) is None or \
                    SERVICE_CATALOG[quote.service_type].domain != domain:
                continue
            if sec.day is not None and e.starts_at is not None and e.starts_at.astimezone(tz).date() != sec.day:
                continue
            match = (e, quote)
            break
        if match is None:
            return text + T("not_in_plan", turn.language), None
        e, quote = match
        line = text + T("in_plan", turn.language, title=self._fmt(turn, e.starts_at),
                        status=msg.status_label(e.status, turn.language))
        eta = None
        if domain == "transport" and e.starts_at is not None and e.status not in ("offered",):
            provider = turn.session.get(ExternalProvider, quote.provider_id)
            minutes = (provider.config or {}).get("estimated_duration_minutes") if provider else None
            if minutes:
                eta = e.starts_at.astimezone(tz) + timedelta(minutes=int(minutes))
                line += "; " + T("arrival_from_plan", turn.language, t=eta.strftime("~%H:%M"))
        return line, eta

    def _answers_open_offers(self, turn: _Turn) -> bool:
        """"Book the transfer and the guide. Skis later." is a decision about
        open offers, not a new multi-part request."""
        from app.transactions.service import TransactionService

        assert self.txn is not None
        if not TransactionService(turn.session, self.txn.deps).open_quotes(turn.conv.id):
            return False
        return any(_DECISION.match(fold(s)) for s in _SENTENCE.split(turn.text) if s.strip())

    @staticmethod
    def _preset(sec: _Section, ctx: _TripContext,
                party: int | None) -> tuple[dict[str, Any], dict[str, str], list[str]]:
        """Trip context fills only what an item lacks, and every inferred
        value is shown in the offer the guest must accept."""
        preset: dict[str, Any] = {}
        partial: dict[str, str] = {}
        inferred: list[str] = []
        spec = SERVICE_CATALOG[sec.service_type or ""]
        keys = {f.key: f.kind for f in spec.fields}
        if party and "party_size" in keys:
            preset["party_size"] = party
        if party and "quantity" in keys:
            preset["quantity"] = party          # "we want skis" for a group of 4 -> 4 sets (shown, confirmable)
            inferred.append("quantity")
        if spec.domain == "transport" and (ctx.arrival is not None or ctx.day is not None):
            when_key = next((k for k, kind in keys.items() if kind == "datetime"), None)
            if when_key and not parse_when(fold(sec.text), (ctx.day or date.today())).time:
                if ctx.arrival is not None:
                    preset[when_key] = ctx.arrival.isoformat()
                    inferred.append(when_key)
                elif ctx.day is not None:
                    partial[when_key] = ctx.day.isoformat()     # "Friday evening": ask the exact time
            if ctx.origin and "pickup" in keys:
                preset["pickup"] = ctx.origin
                inferred.append("pickup")
            if "destination" in keys:
                preset["destination"] = PROPERTY
                inferred.append("destination")
        return preset, partial, inferred

    def _section_label(self, turn: _Turn, sec: _Section) -> str:
        from app.actions.catalog import action_label

        if sec.service_type == "rental" and sec.planned is not None and sec.planned.draft is not None \
                and sec.planned.draft["values"].get("category"):
            return rental_label(sec.planned.draft["values"]["category"], turn.language)
        if sec.service_type == "rental" and sec.planned is not None and sec.planned.quote is not None \
                and sec.planned.quote.request.get("category"):
            return rental_label(sec.planned.quote.request["category"], turn.language)
        if sec.service_type:
            return action_label(sec.service_type, turn.language)
        req = sec.request
        q = req.query if req else None
        if q is not None and req is not None:
            if req.label == "lunch":
                return _loc(SECTION["lunch"], turn.language)
            if req.label == "breakfast":
                return _loc(SECTION["breakfast"], turn.language)
            if req.label in SECTION and req.label not in ("food",):
                return _loc(SECTION[req.label], turn.language)
            if req.label == "food" or q.subcategories & FAMILIES.get("restaurant", set()):
                if q.at is not None and q.at.hour < 16:
                    return _loc(SECTION["lunch"], turn.language)
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
        drafts: list[dict[str, Any]] = []
        shown: list[_Section] = []
        for sec in sections:
            if sec.kind == "statement":
                lines.append("• " + (sec.note or T("noted", turn.language, text=sec.text)))
                continue
            title = self._section_label(turn, sec)
            if sec.kind == "transaction":
                p = sec.planned
                assert p is not None
                if p.outcome == "offered" and p.quote is not None:
                    for i, quote in enumerate(p.quotes or [p.quote]):
                        prefix = f"• {title}: " if i == 0 else "  " + T("or", turn.language) + " "
                        provider = turn.session.get(ExternalProvider, quote.provider_id)
                        line = prefix + (f"{provider.name}: " if provider else "") + \
                            self.txn.offer_line(turn, quote, with_label=False)
                        if sec.at is not None and sec.service_type in ("airport_transfer", "taxi"):
                            line += T("eta", turn.language, t=sec.at.strftime("%H:%M"))
                        conditions = self.txn._conditions(turn, quote).strip()
                        if conditions:
                            line += f". {conditions}"
                        lines.append(line)
                        codes.append(quote.code)
                    self._outcome(turn, "TRANSACTION")
                elif p.outcome == "missing" and p.draft is not None:
                    lines.append("• " + T("need", turn.language, label=title, question=self.txn.question(turn, p.draft)))
                    drafts.append(p.draft)
                elif p.outcome == "unavailable":
                    lines.append(f"• {p.detail}")
                elif p.outcome == "failed":
                    lines.append("• " + T("failed", turn.language, label=title))
                else:
                    lines.append("• " + T("unsupported", turn.language, label=title))
                continue
            q = sec.request.query if sec.request else None
            ctx = self._ctx(turn, sec)
            if sec.request is not None and sec.request.essential and not ctx:
                ctx = T("now", turn.language)
            if not sec.results and not sec.events:
                lines.append("• " + self._nothing_line(turn, sec, title))
                continue
            lines.append(f"• {title}{ctx}{self._anchor_note(turn, sec)}:")
            self._render_discovery(turn, sec, lines, results_state, header=False)
            shown.append(sec)
            if sec.results and not (sec.request and sec.request.essential):
                first = sec.results[0].place
                names = ", ".join(c.place.name for c in sec.results[:1]) + (
                    f" (+{len(sec.results) - 1})" if len(sec.results) > 1 else "")
                itinerary.add(turn.session, stay_id=turn.stay.id, kind=first.category, title=f"{title}: {names}",
                              status=ItemStatus.SHORTLISTED,
                              starts_at=q.at.replace(tzinfo=self._tz(turn)) if q and q.at else None,
                              details={"options": [c.place.id for c in sec.results],
                                       "subcategory": first.subcategory})
        if shown:
            self._outcome(turn, "RECOMMEND" if any(s.request and s.request.recommend for s in shown) else "FIND")
        if codes:
            lines.append(msg.t("trip_footer", turn.language, example=codes[0]))
        if drafts:
            # One question at a time; the rest are asked as each is answered.
            turn.set_state(txn_draft=drafts[0], txn_drafts=drafts[1:] or None)
            lines.append(T("first_question", turn.language, question=self.txn.question(turn, drafts[0])))
        if results_state:
            lines.append(self._footer(turn, shown))
            turn.set_state(last_results=results_state)
        if any(s.results and self._synthetic(s) for s in sections):
            lines.append(T("synthetic", turn.language))
        lines += self._attribution(turn, shown)
        turn.reply = "\n".join(lines)
        turn.succeeded = True
        turn.grounded = True if results_state else turn.grounded
        turn.sources = [f"place:{c.place.slug}" for s in shown for c in s.results] + \
            [f"event:{e.event.slug}" for s in shown for e in s.events]
        return True
