"""Conversational front of the transaction layer (any service family).

Handles, in order:
1. A pending cancellation that needs the guest's go-ahead (a fee applies)
2. A draft that a reply completes, even while other offers are open
3. Open offers the guest is responding to  -> consent / decline / defer / modify
   (several offers may be open at once; consent is read per sentence and
   must name which: "Book the transfer and the guide. Skis later." books two)
4. A draft request still missing details   -> ask only for what is missing
5. Confirmed bookings                      -> cancel (policy checked first) /
                                              change (replacement offer)
6. A new request for a service the property sells through providers

Safety rules:
* Providers and offerings come from discovery over the registry; inventory
  is held while an offer is open; consent counts only for named/just-shown
  offers, with their material terms; "yes" to several offers books nothing.
* Missing details are asked, never guessed (a vague "evening" is not a
  pickup time); details the chosen offering needs (ski heights, rental
  hours) are asked only when that offering needs them.
* A confirmed booking is never mutated silently: a change is a new offer
  that replaces the booking only once the provider confirms it.
* Everything said about a booking afterwards comes from stored state.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.actions.catalog import action_label
from app.agent import messages as msg
from app.agent.authority import transaction_status_message
from app.agent.intents import REQUEST_MARKERS, Intent
from app.agent.policies import AFFIRMATIVE, NEGATIVE
from app.clock import as_utc
from app.db.models import Action, ExternalProvider, ExternalTransaction, Place, Quote, QuoteStatus, RequestType
from app.marketplace import discovery, terms
from app.observability import log_event
from app.marketplace.inventory import NoAvailability
from app.schemas.messages import ActionTaken
from app.text import contains_phrase, fold
from app.transactions.catalog import (
    INTERCITY_PLACES,
    RENTAL_CATEGORIES,
    SERVICE_CATALOG,
    TOPIC_SERVICES,
    VENUE_CATEGORIES,
    ServiceSpec,
    detect_services,
    rental_label,
)
from app.transactions.consent import ConsentDecision, classify, classify_selection, mentioned_code
from app.transactions.format import format_price, format_summary, format_until
from app.transactions.providers.base import ProviderError
from app.transactions.service import QuoteExpired, QuoteNotOpen, TransactionService, TxnDeps
from app.transactions.slots import (
    extract,
    free_text_answer,
    kind_answer,
    local_now,
    parse_count,
    parse_when,
    resolve_datetime,
)

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn

_SAFETY = {Intent.EMERGENCY, Intent.HUMAN_REQUEST, Intent.COMPLAINT}
_AIRPORT = re.compile(r"airport|aerodrom|aeroport|аэропорт|аеродром", re.IGNORECASE)
_AMBIGUOUS = ["cheaper", "discount", "price", "maybe", "more", "think", "not sure", "jeftin*", "popust*", "cijen*",
              "mozda", "razmisl*", "дешевле", "скидк*", "цен*", "может", "подума*", "подробнее"]
_CANCEL = ["cancel*", "otkaz*", "otkaz", "otkazi", "отмен*"]
_CHANGE = ["change", "instead", "move", "reschedule", "different time", "earlier", "later than", "switch",
           "promijen*", "pomjer*", "izmijen*", "ranije", "zamijen*", "измен*", "перенес*", "поменя*", "раньше", "замен*"]
_POLICY = ["policy", "free cancellation", "uslov*", "pravila", "услови*", "правил*"]
_DEFER = ["later", "decide", "not yet", "think about", "hold off", "wait with", "kasnije", "odlucic*", "razmisl*",
          "позже", "потом", "решу", "подумаю", "пока не"]
_DRAFT_DROP = ["never mind", "forget it", "cancel", "ne treba", "zaboravi", "odustajem", "не надо", "отмена", "забудь"]
_TXN_MARKERS = REQUEST_MARKERS + ["want", "we want", "i want", "get us", "get me", "find us", "find me", "find a",
                                  "find an", "organize", "organise", "hocu", "hocemo", "zelimo", "nam treba",
                                  "trebaju nam", "хочу", "хотим", "найди*", "организу*", "закаж*", "нужен", "нужна",
                                  "нужны", "can i rent", "can we rent", "rent", "hire"]
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+")
# Words guests use to point at a booking/offer of a given domain.
DOMAIN_WORDS = {
    "transport": ["taxi", "transfer", "pickup", "pick-up", "driver", "ride", "cab", "taksi", "prevoz", "vozac",
                  "такси", "трансфер*", "водител*"],
    "restaurant": ["table", "dinner", "restaurant", "lunch", "sto", "stol", "vecer*", "restoran*", "столик*",
                   "ужин*", "ресторан*"],
    "nightlife": ["bar", "drinks", "table at the bar", "бар*"],
    "rental": ["rental", "najam", "прокат*", "аренд*"],
    "guide": ["guide", "tour", "hike", "vodic*", "tura", "гид*", "экскурсовод*", "тур"],
}
DOMAIN_CATEGORY = {"transport": "TRANSPORT", "restaurant": "FOOD", "nightlife": "NIGHTLIFE", "rental": "RENTAL",
                   "guide": "GUIDE", "activities": "ACTIVITY", "spa": "WELLNESS", "tickets": "EVENT",
                   "food_delivery": "FOOD"}
# Services where a day without a time can be resolved from the offering's
# schedule (day rentals, fixed tours) - transport always needs the time.
_DAY_RESOLVABLE = {"rental", "guide_booking"}


def _hits(text: str, phrases: list[str]) -> bool:
    f = fold(text)
    return any(contains_phrase(f, p) for p in phrases)


def service_refs(service_type: str, details: dict[str, Any] | None = None) -> list[str]:
    """Words that point at an offer/booking of this service. Rentals are
    told apart by category ("the skis" vs "the e-bike")."""
    spec = SERVICE_CATALOG[service_type]
    if service_type == "rental":
        cat = (details or {}).get("category")
        words = list(RENTAL_CATEGORIES[cat][1]) if cat in RENTAL_CATEGORIES else []
        return words + DOMAIN_WORDS["rental"]
    return list(spec.keywords) + DOMAIN_WORDS.get(spec.domain, [])


def when_key(service_type: str) -> str | None:
    return next((f.key for f in SERVICE_CATALOG[service_type].fields if f.kind == "datetime"), None)


@dataclass
class PlannedOffer:
    """Result of preparing one service without replying (also used by the trip planner)."""

    service_type: str
    outcome: str                       # offered | missing | unavailable | unsupported | failed
    quote: Quote | None = None         # the first (best) offer
    quotes: list[Quote] = field(default_factory=list)
    provider: ExternalProvider | None = None
    missing: list[str] = field(default_factory=list)
    detail: str = ""
    draft: dict[str, Any] | None = None


def new_draft(service_type: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"service_type": service_type, "values": dict(values or {}), "partial": {}, "inferred": [],
            "asked": None, "replaces": None}


class PlanHooks(Protocol):
    """Implemented by the orchestration layer (the stay plan). The
    transaction world announces offers and bookings; it does not know the plan."""

    def offered(self, turn: _Turn, quote: Quote, *, kind: str, title: str, starts_at: datetime | None) -> None: ...

    def booked(self, turn: _Turn, quote: Quote, action_id: str) -> None: ...


class _NoPlan:
    def offered(self, turn: _Turn, quote: Quote, *, kind: str, title: str, starts_at: datetime | None) -> None:
        pass

    def booked(self, turn: _Turn, quote: Quote, action_id: str) -> None:
        pass


class TransactionDialogue:
    def __init__(self, deps: TxnDeps,
                 submit_staff: Callable[[_Turn, str, str, str], Action | None],
                 plan: PlanHooks | None = None) -> None:
        self.deps = deps
        self.submit_staff = submit_staff   # orchestrator: create a staff-queue action
        self.plan = plan or _NoPlan()

    # ================================================================= entry
    def handle(self, turn: _Turn) -> bool:
        intent = turn.intent
        if intent is None or intent.intent in _SAFETY or "billing" in intent.flags:
            return False   # safety and complaints always win
        svc = TransactionService(turn.session, self.deps)
        if turn.state.get("pending_cancel") and self._pending_cancel_turn(turn, svc):
            return True
        quotes = svc.open_quotes(turn.conv.id)
        if self._stale_code_turn(turn, quotes):
            return True
        draft = turn.state.get("txn_draft")
        if draft and quotes and self._absorb(turn, copy.deepcopy(draft)):
            # A trip plan can hold open offers AND a draft that is waiting for
            # a detail ("what time on Sunday?") - an answer goes to the draft.
            return self._draft_turn(turn, svc, draft)
        if quotes and self._quotes_turn(turn, svc, quotes):
            return True
        if draft and self._draft_turn(turn, svc, draft):
            return True
        active = svc.active_for_stay(turn.stay.id)
        if active and self._post_confirmation_turn(turn, svc, active):
            return True
        for service_type in self._requested_services(turn):
            if self.try_start_service(turn, service_type):
                return True
        return False

    def _requested_services(self, turn: _Turn) -> list[str]:
        intent = turn.intent
        out: list[str] = []
        detected = detect_services(turn.text)
        when = parse_when(fold(turn.text), self._now_local(turn).date())
        # A service named together with a time or a group size is a request
        # ("A city tour in German on Saturday at 10, we are 4").
        wants = intent.intent in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST, Intent.UNKNOWN) \
            or _hits(turn.text, _TXN_MARKERS) \
            or bool(detected and (when.day or when.time or parse_count(fold(turn.text))))
        if wants:
            out += detected
        if intent.intent in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST) and intent.request_type:
            topic = list(TOPIC_SERVICES.get(intent.request_type, ()))
            if intent.request_type == RequestType.TRANSPORT and not _AIRPORT.search(turn.text):
                topic.sort(key=lambda s: s != "taxi")
            out += topic
        if any(s in ("airport_transfer", "taxi") for s in out) and not _AIRPORT.search(turn.text):
            out.sort(key=lambda s: s != "taxi")
        if out and out[0] in ("taxi", "airport_transfer") and not _AIRPORT.search(turn.text) \
                and _hits(turn.text, INTERCITY_PLACES):
            out.insert(0, "intercity_transfer")   # "a taxi to Kotor" is a 3-hour intercity transfer
        return list(dict.fromkeys(out))

    def try_start(self, turn: _Turn, topic: RequestType) -> bool:
        """Compatibility entry: start from a request topic."""
        services = list(TOPIC_SERVICES.get(topic, ()))
        if topic == RequestType.TRANSPORT and not _AIRPORT.search(turn.text):
            services.sort(key=lambda s: s != "taxi")
        return any(self.try_start_service(turn, s) for s in services)

    # ================================================================= venues
    def resolve_venue(self, turn: _Turn, service_type: str, text: str) -> Place | None:
        categories = VENUE_CATEGORIES.get(service_type)
        if not categories or not turn.runtime.region:
            return None
        folded = fold(text)
        for place in turn.session.scalars(select(Place).where(Place.region == turn.runtime.region,
                                                              Place.category.in_(categories), Place.active)):
            name = fold(place.name)
            core = re.sub(r"^(konoba|restoran|restaurant|bar|pub|cafe|kafic|pizzeria)\s+", "", name)
            if name in folded or (len(core) > 4 and core in folded):
                return place
        return None

    def offered(self, turn: _Turn, service_type: str) -> bool:
        return turn.runtime.capabilities.service(service_type) is not None

    # ============================================================== drafting
    def try_start_service(self, turn: _Turn, service_type: str, *, preset: dict[str, Any] | None = None,
                          venue: Place | None = None, text: str | None = None) -> bool:
        """`text`: the part of the message this service was asked in ("book
        the first restaurant" out of "book the first restaurant, save the
        second bar and add the concert to Sunday") - details are read from
        it only, so other clauses never leak into this draft."""
        if not self.offered(turn, service_type):
            return False
        spec = SERVICE_CATALOG[service_type]
        needs_venue = any(f.kind == "place_ref" and f.required for f in spec.fields)
        venue = venue or (self.resolve_venue(turn, service_type, turn.text) if needs_venue else None)
        if needs_venue and venue is None:
            return False   # e.g. "book a table" without a venue -> the property's own restaurant (staff)
        draft = new_draft(service_type)
        if venue is not None:
            draft["values"]["venue"], draft["values"]["_venue_id"] = venue.name, venue.id
        draft["values"].update(preset or {})
        self._absorb(turn, draft, text=text)
        log_event("transaction_draft_started", conversation_id=turn.conv.id, service_type=service_type)
        return self._advance(turn, TransactionService(turn.session, self.deps), draft)

    def prepare(self, turn: _Turn, service_type: str, text: str, *, preset: dict[str, Any] | None = None,
                preset_partial: dict[str, str] | None = None, inferred: list[str] | None = None,
                venue: Place | None = None) -> PlannedOffer:
        """Prepare one service of a multi-part request without replying:
        offers, or the details still missing (never guessed)."""
        if not self.offered(turn, service_type):
            return PlannedOffer(service_type, "unsupported")
        draft = new_draft(service_type)
        if venue is not None:
            draft["values"]["venue"], draft["values"]["_venue_id"] = venue.name, venue.id
        self._absorb(turn, draft, text=text)
        for key, value in (preset or {}).items():
            if key not in draft["values"] and key not in draft["partial"]:
                draft["values"][key] = value
                if key in (inferred or []):
                    draft["inferred"] = sorted(set(draft["inferred"]) | {key})
        for key, day in (preset_partial or {}).items():
            if key not in draft["values"] and key not in draft["partial"]:
                draft["partial"][key] = day
        return self.resolve(turn, TransactionService(turn.session, self.deps), draft)

    def question(self, turn: _Turn, draft: dict[str, Any]) -> str:
        asked = draft["asked"]
        spec_field = next((f for f in self._spec(draft).fields if f.key == asked), None)
        if spec_field is not None and spec_field.kind == "datetime" and draft["partial"].get(asked):
            day = date.fromisoformat(draft["partial"][asked])
            return msg.t("ask_time_on", turn.language, day=day.strftime("%d.%m.%Y"))
        if spec_field is None:
            return msg.t("ask_text", turn.language, field=asked)
        key = f"ask_{spec_field.key}" if f"ask_{spec_field.key}" in msg.CATALOG else f"ask_{spec_field.kind}"
        if key not in msg.CATALOG:
            key = "ask_text"
        label = spec_field.labels.get(turn.language.split("-")[0], spec_field.key)
        qty = draft["values"].get("quantity") or draft["values"].get("party_size") or ""
        item = rental_label(draft["values"].get("category", ""), turn.language) if draft["values"].get("category") \
            else ""
        return msg.t(key, turn.language, field=label, n=str(qty), item=item)

    def _spec(self, draft: dict[str, Any]) -> ServiceSpec:
        return SERVICE_CATALOG[draft["service_type"]]

    def _now_local(self, turn: _Turn) -> datetime:
        return local_now(self.deps.clock.now(), turn.runtime.timezone)

    def _absorb(self, turn: _Turn, draft: dict[str, Any], text: str | None = None) -> bool:
        """Merge details from the guest's message into the draft."""
        text = turn.text if text is None else text
        spec = self._spec(draft)
        now_local = self._now_local(turn)
        ex = extract(text, spec, now_local.date())
        changed = False
        for key, value in ex.values.items():
            if draft["values"].get(key) != value:
                draft["values"][key], changed = value, True
        for key, parts in ex.when.items():
            if parts.approximate and spec.domain == "transport":
                parts.time = None   # "Friday evening" is not a pickup time - ask for one
            previous = datetime.fromisoformat(draft["values"][key]) if draft["values"].get(key) else None
            partial = date.fromisoformat(draft["partial"][key]) if draft["partial"].get(key) else None
            dt, pending = resolve_datetime(parts, previous, now_local, partial)
            if dt is not None:
                if draft["values"].get(key) != dt.isoformat():
                    draft["values"][key], changed = dt.isoformat(), True
                draft["partial"].pop(key, None)
            elif pending is not None:
                if draft["partial"].get(key) != pending.isoformat() or key in draft["values"]:
                    changed = True
                draft["partial"][key] = pending.isoformat()
                draft["values"].pop(key, None)
        draft["inferred"] = sorted(set(draft["inferred"]) | ex.inferred)
        asked = draft.get("asked")
        non_answer = (turn.intent is not None and turn.intent.intent == Intent.GENERAL_CONVERSATION) \
            or fold(text).strip(" .!") in AFFIRMATIVE | NEGATIVE
        if not changed and asked and not non_answer:
            spec_field = next((f for f in spec.fields if f.key == asked), None)
            if spec_field is None:
                return False
            if spec_field.kind == "count" and re.fullmatch(r"\s*(\d{1,2})\s*", text):
                draft["values"][asked], changed = int(text.strip()), True
            elif spec_field.kind == "place_ref":
                venue = self.resolve_venue(turn, draft["service_type"], text)
                if venue is not None:
                    draft["values"]["venue"], draft["values"]["_venue_id"], changed = venue.name, venue.id, True
            elif spec_field.kind in ("place", "text"):
                if (answer := free_text_answer(text, spec_field.kind)) is not None:
                    draft["values"][asked], changed = answer, True
            elif (answer := kind_answer(text, spec_field.kind)) is not None:
                draft["values"][asked], changed = answer, True
        return changed

    def _fill_from_stay(self, turn: _Turn, draft: dict[str, Any]) -> None:
        """Party size the guest already told us (or staff verified) - shown
        in the offer, so the guest confirms it together with the price."""
        for spec_field in self._spec(draft).fields:
            if spec_field.kind == "count" and spec_field.key not in draft["values"]:
                verified = turn.stay.party_size
                stated = (turn.stay.facts or {}).get("guest_count", {}).get("value")
                if verified or stated:
                    draft["values"][spec_field.key] = verified or stated

    def _missing(self, draft: dict[str, Any]) -> list[str]:
        out = []
        for f in self._spec(draft).fields:
            if not f.required or f.key in draft["values"]:
                continue
            if f.kind == "datetime" and draft["partial"].get(f.key) and draft["service_type"] in _DAY_RESOLVABLE:
                continue   # the offering's schedule may resolve a day-only request
            out.append(f.key)
        return out

    def _advance(self, turn: _Turn, svc: TransactionService, draft: dict[str, Any], prefix: str = "") -> bool:
        planned = self.resolve(turn, svc, draft)
        return self._reply_planned(turn, planned, prefix)

    def _reply_planned(self, turn: _Turn, planned: PlannedOffer, prefix: str = "") -> bool:
        if planned.outcome == "missing" and planned.draft is not None:
            turn.set_state(txn_draft=planned.draft)
            turn.reply = prefix + self.question(turn, planned.draft)
            # A follow-up question does not withdraw the offers just shown.
            turn.offered_quote_ids.extend(turn.state.get("awaiting_quotes") or [])
            turn.succeeded = True
            return True
        turn.set_state(txn_draft=None)
        if planned.outcome == "offered":
            if len(planned.quotes) > 1:
                turn.reply = prefix + self._options_text(turn, planned.quotes)
            else:
                assert planned.quote is not None
                turn.reply = prefix + self._quote_text(turn, planned.quote)
        elif planned.outcome == "failed":
            provider = planned.provider
            self.submit_staff(turn, "staff_question", turn.text,
                              f"Guest wants {planned.service_type}, provider "
                              f"{provider.slug if provider else '?'} quote failed")
            turn.reply = prefix + msg.t("quote_unavailable", turn.language,
                                        provider=provider.name if provider else "")
        else:   # unavailable / unsupported: say so, with real alternatives
            turn.reply = prefix + planned.detail
        turn.reply = self._next_queued(turn, turn.reply)
        turn.succeeded = True
        return True

    def _next_queued(self, turn: _Turn, reply: str) -> str:
        """A trip plan may leave several items waiting for a detail: once one
        is settled, ask about the next."""
        queue = list(turn.state.get("txn_drafts") or [])
        if not queue or turn.state.get("txn_draft"):
            return reply
        nxt = queue.pop(0)
        turn.set_state(txn_draft=nxt, txn_drafts=queue or None)
        return f"{reply}\n{self.question(turn, nxt)}"

    def _draft_turn(self, turn: _Turn, svc: TransactionService, draft: dict[str, Any]) -> bool:
        turn.offered_quote_ids.extend(turn.state.get("awaiting_quotes") or [])   # still in scope while asking
        if _hits(turn.text, _DRAFT_DROP) and len(turn.text.split()) <= 4:
            turn.set_state(txn_draft=None)
            turn.reply, turn.succeeded = msg.t("txn_draft_dropped", turn.language), True
            turn.reply = self._next_queued(turn, turn.reply)
            return True
        if self._absorb(turn, draft):
            return self._advance(turn, svc, draft)
        if turn.intent and turn.intent.intent in (Intent.UNKNOWN, Intent.GENERAL_CONVERSATION):
            return self._advance(turn, svc, draft)   # re-ask the missing detail
        return False   # the guest moved on; the draft waits

    # ============================================================ resolution
    def resolve(self, turn: _Turn, svc: TransactionService, draft: dict[str, Any]) -> PlannedOffer:
        """Draft -> missing detail | offers | honest unavailability. No reply."""
        service_type = draft["service_type"]
        self._fill_from_stay(turn, draft)
        missing = self._missing(draft)
        if missing:
            draft["asked"] = missing[0]
            return PlannedOffer(service_type, "missing", missing=missing, draft=draft)
        values = draft["values"]
        wk = when_key(service_type)
        start = as_utc(datetime.fromisoformat(values[wk])) if wk and values.get(wk) else None
        day = date.fromisoformat(draft["partial"][wk]) if wk and draft["partial"].get(wk) else None
        if day is None and start is None and values.get("_event_day"):
            day = date.fromisoformat(values["_event_day"])     # a ticket: the event's own date
        cap = turn.runtime.capabilities.service(service_type)
        details = {k: v for k, v in values.items() if not k.startswith("_")}
        result = discovery.discover(
            turn.session, service_type=service_type, details=details, region=turn.runtime.region,
            property_id=turn.runtime.property_id, now=self.deps.clock.now(), tz=turn.runtime.timezone,
            declared=cap.provider if cap else None, start=start, day=day, venue_id=values.get("_venue_id"),
            event_id=values.get("_event_id"), replaces=draft.get("replaces"))
        log_event("provider_discovery", service_type=service_type, ready=len(result.candidates),
                  pending=len(result.pending), reason=result.reason,
                  top=[(c.provider.slug, c.offering.slug if c.offering else None) for c in result.candidates[:3]])
        if not result.candidates and result.pending:
            need = result.pending[0].missing[0]
            draft["asked"] = wk if need == "start_time" and wk else need
            return PlannedOffer(service_type, "missing", missing=result.pending[0].missing, draft=draft)
        if not result.candidates:
            return PlannedOffer(service_type, "unsupported" if result.reason == "category_unsupported"
                                else "unavailable", detail=self._unavailable_text(turn, draft, result))
        by_format = service_type == "guide_booking" and not values.get("format")
        chosen = discovery.options(result, by_format=by_format)
        quotes: list[Quote] = []
        replaces = turn.session.get(ExternalTransaction, draft["replaces"]) if draft.get("replaces") else None
        for i, cand in enumerate(chosen):
            try:
                quote = svc.request_quote(stay=turn.stay, conv=turn.conv, provider=cand.provider,
                                          service_type=service_type, details=values, locale=turn.language,
                                          candidate=cand, supersede=(i == 0), replaces=replaces)
            except NoAvailability:
                continue   # taken a moment ago by someone else
            except ProviderError as exc:
                log_event("quote_failed", provider=cand.provider.slug, error=f"{exc.kind}: {exc}")
                if not quotes and i == len(chosen) - 1:
                    return PlannedOffer(service_type, "failed", provider=cand.provider)
                continue
            self._register_quote(turn, quote)
            quotes.append(quote)
        if not quotes:
            return PlannedOffer(service_type, "unavailable",
                                detail=self._unavailable_text(turn, draft, discovery.DiscoveryResult([], [], "slot_gone")))
        return PlannedOffer(service_type, "offered", quote=quotes[0], quotes=quotes,
                            provider=turn.session.get(ExternalProvider, quotes[0].provider_id))

    def _register_quote(self, turn: _Turn, quote: Quote) -> None:
        spec = SERVICE_CATALOG[quote.service_type]
        wk = when_key(quote.service_type)
        starts = datetime.fromisoformat(quote.request[wk]) if wk and quote.request.get(wk) else None
        self.plan.offered(turn, quote, kind=DOMAIN_CATEGORY.get(spec.domain, "OTHER"),
                          title=self._summary(turn, quote), starts_at=starts)
        turn.offered_quote_ids.append(quote.id)
        turn.actions.append(ActionTaken(kind="quote", id=quote.id, detail={
            "service_type": quote.service_type, "status": quote.status.value, "amount": str(quote.amount),
            "currency": quote.currency, "code": quote.code}))

    # Iteration 3 name kept for the trip planner and tests.
    def make_offer(self, turn: _Turn, svc: TransactionService, provider: ExternalProvider | None,
                   service_type: str, details: dict[str, Any]) -> PlannedOffer:
        return self.resolve(turn, svc, new_draft(service_type, details))

    def _unavailable_text(self, turn: _Turn, draft: dict[str, Any], result: discovery.DiscoveryResult) -> str:
        tz = ZoneInfo(turn.runtime.timezone)
        service_type = draft["service_type"]
        label = action_label(service_type, turn.language)
        if service_type == "rental" and draft["values"].get("category"):
            label = f"{label} ({rental_label(draft['values']['category'], turn.language)})"
        if result.reason == "category_unsupported":
            return msg.t("rental_unsupported", turn.language,
                         item=rental_label(draft["values"].get("category", ""), turn.language))
        alts = []
        for m in result.alternatives:
            title = (m.offering.title or {}).get(turn.language.split("-")[0]) or (m.offering.title or {}).get("en") \
                or m.offering.slug
            start = as_utc(m.starts_at or m.slot.starts_at).astimezone(tz)
            alts.append(f"{title} {start.strftime('%d.%m %H:%M')}")
        alt_text = msg.t("alternatives", turn.language, list="; ".join(alts)) if alts else ""
        reason_key = f"reason_{result.reason}"
        reason = msg.t(reason_key, turn.language) if reason_key in msg.CATALOG else (result.reason or "")
        return msg.t("no_availability", turn.language, service=label, reason=reason, alternatives=alt_text)

    def _summary(self, turn: _Turn, quote: Quote, *, with_label: bool = True) -> str:
        details = {k: v for k, v in quote.request.items() if not k.startswith("_") and k != "weather_dependent"}
        if quote.service_type == "rental" and details.get("category"):
            details["category"] = rental_label(details["category"], turn.language)
        text = format_summary(quote.service_type, details, turn.language, turn.runtime.timezone, turn.prop.name,
                              with_label=with_label)
        if quote.request.get("offering") and quote.service_type not in VENUE_CATEGORIES:
            text += f"; {quote.request['offering']}"
        return text

    def _conditions(self, turn: _Turn, quote: Quote) -> str:
        parts = []
        if quote.conditions:
            parts.append(quote.conditions.rstrip(".") + ". ")
        rendered = terms.render(quote.terms or {}, turn.language)
        if rendered:
            parts.append(rendered)
        return "".join(parts)

    def _quote_text(self, turn: _Turn, quote: Quote, key: str = "quote_offer") -> str:
        tz = turn.runtime.timezone
        provider = turn.session.get(ExternalProvider, quote.provider_id)
        return msg.t(
            key, turn.language, provider=provider.name if provider else "", price=format_price(quote.amount, quote.currency),
            summary=self._summary(turn, quote), conditions=self._conditions(turn, quote),
            valid_until=format_until(quote.valid_until, tz, self.deps.clock.now()), code=quote.code,
        )

    def _options_text(self, turn: _Turn, quotes: list[Quote]) -> str:
        lines = [msg.t("quote_options_intro", turn.language, n=str(len(quotes)))]
        for i, q in enumerate(quotes, 1):
            provider = turn.session.get(ExternalProvider, q.provider_id)
            lines.append(f"{i}) {provider.name if provider else ''}: {self._summary(turn, q, with_label=False)} - "
                         f"{format_price(q.amount, q.currency)} ({q.code}). {self._conditions(turn, q)}".rstrip())
        lines.append(msg.t("quote_options_footer", turn.language, example=quotes[0].code,
                           valid_until=format_until(quotes[0].valid_until, turn.runtime.timezone,
                                                    self.deps.clock.now())))
        return "\n".join(lines)

    def offer_line(self, turn: _Turn, quote: Quote, *, with_label: bool = True) -> str:
        return f"{self._summary(turn, quote, with_label=with_label)} - " \
               f"{format_price(quote.amount, quote.currency)} ({quote.code})"

    def _draft_from(self, request: dict[str, Any], service_type: str) -> dict[str, Any]:
        values = {k: v for k, v in request.items()
                  if k in ("_venue_id", "_event_id", "_event_day") or not (k.startswith("_") or k in ("offering", "weather_dependent"))}
        return new_draft(service_type, values)

    def _modified(self, turn: _Turn, quote: Quote) -> dict[str, Any] | None:
        draft = self._draft_from(quote.request, quote.service_type)
        before = copy.deepcopy(draft["values"])
        if self._absorb(turn, draft) and (draft["values"] != before or draft["partial"]):
            return draft
        return None

    # ====================================================== responding to offers
    def _quotes_turn(self, turn: _Turn, svc: TransactionService, quotes: list[Quote]) -> bool:
        awaiting_ids = set(turn.state.get("awaiting_quotes") or [])
        code = mentioned_code(turn.text)
        refs_of = {q.id: service_refs(q.service_type, q.request) for q in quotes}
        referenced = [q for q in quotes if q.code == code or _hits(turn.text, refs_of[q.id])]
        awaiting = [q for q in quotes if q.id in awaiting_ids]
        candidates = referenced or awaiting
        refs = [q.code for q in quotes] + [w for q in quotes for w in refs_of[q.id]]
        if not candidates and classify_selection(turn.text, refs) == (ConsentDecision.CONSENT, True):
            candidates = quotes   # "book all" names every open offer explicitly
        if not candidates:
            return False   # consent must refer to an offer the guest was just shown or names

        sentences = [x for x in _SENTENCES.split(turn.text) if x.strip()]
        if len(sentences) > 1 and self._per_sentence(turn, svc, quotes, refs_of, refs, sentences):
            return True

        fresh_request = bool(detect_services(turn.text)) and not referenced
        if fresh_request and classify_selection(turn.text, refs)[0] == ConsentDecision.NONE:
            return False   # a new request ("we also need skis") - not an answer to the open offer
        if len(candidates) == 1 and not fresh_request:
            quote = candidates[0]
            new_draft_ = self._modified(turn, quote)
            if new_draft_ is not None:
                new_draft_["replaces"] = quote.replaces_transaction_id
                return self._advance(turn, svc, new_draft_, prefix=msg.t("quote_updated", turn.language))
        if len(quotes) == 1 and not referenced:
            decision, all_req = classify(turn.text, quotes[0].code, False), False
        else:
            decision, all_req = classify_selection(turn.text, refs)
        log_event("consent_decision", quotes=[q.id for q in candidates], decision=decision.value, all=all_req)

        if decision == ConsentDecision.CONSENT:
            targets = quotes if all_req else candidates
            if len(targets) > 1 and not all_req and not referenced:
                return self._which_offer(turn, targets)
            return self._accept_all(turn, svc, targets)
        if decision == ConsentDecision.DECLINE:
            for q in candidates:
                svc.decline(q)
            turn.reply, turn.succeeded = msg.t("quote_declined", turn.language), True
            return True
        intent = turn.intent.intent if turn.intent else Intent.UNKNOWN
        if intent in (Intent.UNKNOWN, Intent.GENERAL_CONVERSATION) or _hits(turn.text, _AMBIGUOUS):
            if len(candidates) > 1:
                return self._which_offer(turn, candidates)
            quote = candidates[0]
            turn.reply = self._quote_text(turn, quote, key="quote_clarify")
            turn.offered_quote_ids.append(quote.id)
            turn.succeeded = True
            return True
        return False

    def _stale_code_turn(self, turn: _Turn, open_quotes: list[Quote]) -> bool:
        """The guest names a code that is no longer open (replaced, expired,
        declined): say so explicitly - it can never be booked - and show the
        current offer for that service, if any."""
        code = mentioned_code(turn.text)
        if not code or any(q.code == code for q in open_quotes):
            return False
        stale = turn.session.scalar(select(Quote).where(Quote.conversation_id == turn.conv.id, Quote.code == code)
                                    .order_by(Quote.created_at.desc()).limit(1))
        if stale is None or stale.status in (QuoteStatus.OFFERED, QuoteStatus.ACCEPTED_BY_GUEST):
            return False
        key = "quote_replaced" if stale.status == QuoteStatus.SUPERSEDED else "quote_no_longer_valid"
        reply = msg.t(key, turn.language, code=code)
        current = next((q for q in open_quotes if q.service_type == stale.service_type), None)
        if current is not None:
            reply += " " + self._quote_text(turn, current)
            turn.offered_quote_ids.append(current.id)
        turn.reply, turn.succeeded = reply, True
        log_event("stale_quote_referenced", quote_id=stale.id, status=stale.status.value)
        return True

    def _per_sentence(self, turn: _Turn, svc: TransactionService, quotes: list[Quote], refs_of: dict[str, list[str]],
                      refs: list[str], sentences: list[str]) -> bool:
        """"Book the transfer and the guide. I'll decide about the skis
        later." -> consent to two offers, the third stays open. Every
        sentence must be a clear decision (or a deferral), else nothing."""
        to_accept: list[Quote] = []
        to_decline: list[Quote] = []
        deferred: list[Quote] = []
        for sentence in sentences:
            named = [q for q in quotes if q.code == mentioned_code(sentence) or _hits(sentence, refs_of[q.id])]
            if _hits(sentence, _DEFER):
                deferred += named
                continue
            decision, all_req = classify_selection(sentence, refs)
            if decision == ConsentDecision.NONE:
                if named or fold(sentence).strip(" .!") not in ("thanks", "thank you", "hvala", "спасибо", "please"):
                    return False
                continue
            targets = quotes if all_req else named
            if not targets:
                return False   # "Yes." on its own, with several open offers: ambiguous
            (to_accept if decision == ConsentDecision.CONSENT else to_decline).extend(targets)
        if not (to_accept or to_decline):
            return False
        deferred_ids = {q.id for q in deferred}
        to_accept = [q for q in dict.fromkeys(to_accept) if q.id not in deferred_ids]
        for q in dict.fromkeys(to_decline):
            if q.id not in deferred_ids:
                svc.decline(q)
        if to_accept:
            self._accept_all(turn, svc, to_accept)
        else:
            turn.reply = msg.t("quote_declined", turn.language)
        kept = [q for q in quotes if q.id in deferred_ids and q.status == QuoteStatus.OFFERED]
        for q in kept:
            turn.reply += "\n" + msg.t("offer_kept_open", turn.language, offer=self.offer_line(turn, q),
                                       valid_until=format_until(q.valid_until, turn.runtime.timezone,
                                                                self.deps.clock.now()))
        turn.succeeded = True
        return True

    def _which_offer(self, turn: _Turn, quotes: list[Quote]) -> bool:
        turn.reply = msg.t("which_offer", turn.language,
                           offers="; ".join(self.offer_line(turn, q) for q in quotes), example=quotes[0].code)
        turn.offered_quote_ids.extend(q.id for q in quotes)
        turn.succeeded = True
        return True

    def _accept_all(self, turn: _Turn, svc: TransactionService, quotes: list[Quote]) -> bool:
        lines = []
        for quote in quotes:
            try:
                txn = svc.accept(quote, message_id=turn.guest_message_id, text=turn.text)
            except QuoteExpired:
                draft = self._draft_from(quote.request, quote.service_type)
                draft["replaces"] = quote.replaces_transaction_id
                planned = self.resolve(turn, svc, draft)
                if planned.quote is not None:
                    lines.append(msg.t("quote_expired", turn.language) + self._quote_text(turn, planned.quote))
                else:
                    lines.append(msg.t("quote_expired", turn.language) + planned.detail)
                continue
            except NoAvailability as exc:
                lines.append(self._unavailable_text(turn, self._draft_from(quote.request, quote.service_type),
                                                    discovery.DiscoveryResult([], [], exc.reason)))
                continue
            except QuoteNotOpen:
                continue
            self.plan.booked(turn, quote, txn.action_id)
            lines.append(self._report_line(turn, svc, txn))
        if not lines:
            return False
        turn.reply = "\n".join(lines)
        turn.succeeded = True
        return True

    def _report_line(self, turn: _Turn, svc: TransactionService, txn: ExternalTransaction) -> str:
        action = txn.action
        turn.actions.append(ActionTaken(kind="transaction", id=action.id, detail={
            "action_type": action.action_type, "status": action.status.value, "quote_id": txn.quote_id,
            "transaction_id": txn.id}))
        return transaction_status_message(svc.view(txn, action, turn.prop, turn.language), turn.language)

    # ====================================================== after consent
    def _target_booking(self, turn: _Turn, active: list[ExternalTransaction]) -> ExternalTransaction | None | bool:
        """The booking the message is about; None = not about one; False =
        ambiguous (asked which)."""
        referenced = [t for t in active if _hits(turn.text, service_refs(t.action.action_type, t.request))]
        if len(referenced) == 1:
            return referenced[0]
        if not referenced and len(active) == 1:
            return active[0] if len(turn.text.split()) <= 4 else None
        turn.reply = msg.t("which_booking", turn.language, bookings="; ".join(
            f"{self._booking_label(turn, t)} ({t.provider.name})" for t in (referenced or active)))
        turn.succeeded = True
        return False

    def _booking_label(self, turn: _Turn, txn: ExternalTransaction) -> str:
        label = action_label(txn.action.action_type, turn.language)
        if txn.action.action_type == "rental" and txn.request.get("category"):
            label += f" ({rental_label(txn.request['category'], turn.language)})"
        return label

    def _post_confirmation_turn(self, turn: _Turn, svc: TransactionService,
                                active: list[ExternalTransaction]) -> bool:
        wants_cancel = _hits(turn.text, _CANCEL) and not _hits(turn.text, _POLICY)
        wants_change = _hits(turn.text, _CHANGE) or (not wants_cancel and _hits(turn.text, ["to", "na", "на"])
                                                     and bool(re.search(r"\b(move|make it|switch)\b", fold(turn.text))))
        if not (wants_cancel or wants_change):
            return False
        txn = self._target_booking(turn, active)
        if txn is False:
            return True
        if txn is None:
            return False
        assert isinstance(txn, ExternalTransaction)
        if wants_cancel:
            return self._cancel(turn, svc, txn)
        return self._change(turn, svc, txn)

    def _cancel(self, turn: _Turn, svc: TransactionService, txn: ExternalTransaction, *, confirmed: bool = False) -> bool:
        policy = svc.cancellation_policy(txn)
        tz = turn.runtime.timezone
        if policy.status == "fee" and not confirmed:
            # Never promise a free cancellation the provider does not give.
            turn.set_state(pending_cancel={"transaction_id": txn.id})
            turn.reply = msg.t("cancel_fee_warning", turn.language, service=self._booking_label(turn, txn),
                               provider=txn.provider.name, fee=policy.fee or "",
                               deadline=as_utc(policy.deadline).astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M")
                               if policy.deadline else "-")
            turn.succeeded = True
            return True
        svc.request_cancel(txn)
        if txn.action.status.value == "cancelled":
            turn.reply = self._report_line(turn, svc, txn)
        else:
            note = msg.t("cancel_free_note", turn.language) if policy.status == "free" else ""
            turn.reply = note + msg.t("txn_cancel_requested", turn.language, provider=txn.provider.name,
                                      service=self._booking_label(turn, txn))
        turn.succeeded = True
        return True

    def _pending_cancel_turn(self, turn: _Turn, svc: TransactionService) -> bool:
        pending = turn.state["pending_cancel"]
        turn.set_state(pending_cancel=None)
        txn = turn.session.get(ExternalTransaction, pending["transaction_id"])
        answer = fold(turn.text).strip(" .!")
        if txn is None:
            return False
        if answer in NEGATIVE or answer.startswith(("no", "ne ", "нет")):
            turn.reply, turn.succeeded = msg.t("cancel_kept", turn.language,
                                               service=self._booking_label(turn, txn)), True
            return True
        if answer in AFFIRMATIVE or re.fullmatch(r"(yes|da|да)[, ]*(please )?(cancel( it)?|otkazi|отмени(те)?)?",
                                                 answer):
            return self._cancel(turn, svc, txn, confirmed=True)
        return False

    def _change(self, turn: _Turn, svc: TransactionService, txn: ExternalTransaction) -> bool:
        """A confirmed booking is never mutated silently: the change becomes a
        NEW offer that replaces the booking once the provider confirms it."""
        draft = self._draft_from(txn.request, txn.action.action_type)
        if txn.quote.request.get("_venue_id"):
            draft["values"]["_venue_id"] = txn.quote.request["_venue_id"]
        before = copy.deepcopy(draft["values"])
        if not self._absorb(turn, draft) or draft["values"] == before and not draft["partial"]:
            self.submit_staff(turn, "staff_question", turn.text,
                              f"Guest asks to change confirmed {txn.action.action_type} "
                              f"(ref {txn.provider_reference or txn.id}): {turn.text}")
            turn.reply = msg.t("txn_change_after_confirm", turn.language, provider=txn.provider.name,
                               service=self._booking_label(turn, txn))
            turn.succeeded = True
            return True
        draft["replaces"] = txn.id
        policy = svc.cancellation_policy(txn)
        policy_note = msg.t("change_policy_free", turn.language) if policy.status == "free" else (
            msg.t("change_policy_fee", turn.language, fee=policy.fee or "") if policy.status == "fee" else "")
        prefix = msg.t("change_offer", turn.language, service=self._booking_label(turn, txn),
                       reference=txn.provider_reference or "-", policy=policy_note)
        log_event("transaction_change_requested", transaction_id=txn.id)
        return self._advance(turn, svc, draft, prefix=prefix)
