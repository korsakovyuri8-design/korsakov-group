"""Conversational front of the transaction layer (any service type).

Handles, in order:
1. Open offers the guest is responding to   -> consent / decline / modify / clarify
   (several offers may be open at once - a trip plan; consent must name which)
2. A draft request still missing details    -> ask only for what is missing
3. Confirmed bookings                       -> cancel / change-after-confirmation (targeted)
4. A new request for a service the property can sell through a provider

Safety rules:
* Consent counts only for offers the bot just presented or that the guest
  names (code or service), only if the message is an explicit affirmative,
  and only before the offer expires. "yes" with several open offers is
  ambiguous and books nothing. Changed details => a new offer.
* Missing details are asked, never guessed; contextual inferences are shown
  in the offer the guest must accept.
* Availability comes only from recorded inventory or the provider.
* Everything said about a booking afterwards comes from stored state.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.clock import as_utc

from app.actions.catalog import action_label
from app.agent import messages as msg
from app.agent.authority import transaction_status_message
from app.agent.intents import REQUEST_MARKERS, Intent
from app.agent.policies import AFFIRMATIVE, NEGATIVE
from app.db.models import Action, ExternalProvider, ExternalTransaction, Offering, Place, Quote, RequestType
from app.observability import log_event
from app.places.availability import NoAvailability
from app.schemas.messages import ActionTaken
from app.text import contains_phrase, fold
from app.transactions.catalog import SERVICE_CATALOG, TOPIC_SERVICES, VENUE_CATEGORIES, ServiceSpec, detect_services
from app.transactions.consent import ConsentDecision, classify, classify_selection, mentioned_code
from app.transactions.format import format_price, format_summary, format_until
from app.transactions.providers.base import ProviderError
from app.transactions.service import QuoteExpired, QuoteNotOpen, TransactionService, TxnDeps
from app.transactions.slots import extract, free_text_answer, local_now, resolve_datetime
from app.trip import itinerary

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn

_SAFETY = {Intent.EMERGENCY, Intent.HUMAN_REQUEST, Intent.COMPLAINT}
_AIRPORT = re.compile(r"airport|aerodrom|aeroport|аэропорт|аеродром", re.IGNORECASE)
_AMBIGUOUS = ["cheaper", "discount", "price", "maybe", "more", "think", "not sure", "jeftin*", "popust*", "cijen*",
              "mozda", "razmisl*", "дешевле", "скидк*", "цен*", "может", "подума*", "подробнее"]
_CANCEL = ["cancel*", "otkaz*", "otkaz", "otkazi", "отмен*"]
_CHANGE = ["change", "instead", "move", "reschedule", "different time", "earlier", "later",
           "promijen*", "pomjer*", "izmijen*", "ranije", "kasnije", "измен*", "перенес*", "поменя*", "раньше", "позже"]
_POLICY = ["policy", "free cancellation", "uslov*", "pravila", "услови*", "правил*"]
_DRAFT_DROP = ["never mind", "forget it", "cancel", "ne treba", "zaboravi", "odustajem", "не надо", "отмена", "забудь"]
_TXN_MARKERS = REQUEST_MARKERS + ["want", "we want", "i want", "get us", "get me", "find us", "find me", "find a",
                                  "find an", "organize", "organise", "hocu", "hocemo", "zelimo", "nam treba",
                                  "trebaju nam", "хочу", "хотим", "найди*", "организу*", "закаж*", "нужен", "нужна",
                                  "нужны"]
# Words guests use to point at a booking/offer of a given domain.
DOMAIN_WORDS = {
    "transport": ["taxi", "transfer", "pickup", "pick-up", "driver", "ride", "cab", "taksi", "prevoz", "vozac",
                  "такси", "трансфер*", "водител*"],
    "restaurant": ["table", "dinner", "restaurant", "lunch", "sto", "stol", "vecer*", "restoran*", "столик*",
                   "ужин*", "ресторан*"],
    "nightlife": ["bar", "drinks", "table at the bar", "бар*"],
    "ski_rental": ["ski", "skis", "skije", "skija", "лыж*"],
    "guide": ["guide", "vodic*", "гид*", "экскурсовод*"],
}
DOMAIN_CATEGORY = {"transport": "TRANSPORT", "restaurant": "FOOD", "nightlife": "NIGHTLIFE", "ski_rental": "RENTAL",
                   "car_rental": "RENTAL", "guide": "GUIDE", "activities": "ACTIVITY", "spa": "WELLNESS",
                   "tickets": "EVENT", "food_delivery": "FOOD"}


def _hits(text: str, phrases: list[str]) -> bool:
    f = fold(text)
    return any(contains_phrase(f, p) for p in phrases)


def service_refs(service_type: str) -> list[str]:
    spec = SERVICE_CATALOG[service_type]
    return list(spec.keywords) + DOMAIN_WORDS.get(spec.domain, [])


@dataclass
class PlannedOffer:
    """Result of preparing one service without replying (used by the trip planner)."""

    service_type: str
    outcome: str                       # offered | missing | unavailable | unsupported | failed
    quote: Quote | None = None
    provider: ExternalProvider | None = None
    missing: list[str] = field(default_factory=list)
    detail: str = ""
    draft: dict[str, Any] | None = None


class TransactionDialogue:
    def __init__(self, deps: TxnDeps,
                 submit_staff: Callable[[_Turn, str, str, str], Action | None]) -> None:
        self.deps = deps
        self.submit_staff = submit_staff   # orchestrator: create a staff-queue action

    # ================================================================= entry
    def handle(self, turn: _Turn) -> bool:
        intent = turn.intent
        if intent is None or intent.intent in _SAFETY or "billing" in intent.flags:
            return False   # safety and complaints always win
        svc = TransactionService(turn.session, self.deps)
        quotes = svc.open_quotes(turn.conv.id)
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
        wants = intent.intent in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST, Intent.UNKNOWN) \
            or _hits(turn.text, _TXN_MARKERS)
        if wants:
            out += detect_services(turn.text)
        if intent.intent in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST) and intent.request_type:
            topic = list(TOPIC_SERVICES.get(intent.request_type, ()))
            if intent.request_type == RequestType.TRANSPORT and not _AIRPORT.search(turn.text):
                topic.reverse()
            out += topic
        if any(s in ("airport_transfer", "taxi") for s in out) and not _AIRPORT.search(turn.text):
            out.sort(key=lambda s: s != "taxi")
        return list(dict.fromkeys(out))

    def try_start(self, turn: _Turn, topic: RequestType) -> bool:
        """Compatibility entry: start from a request topic."""
        services = list(TOPIC_SERVICES.get(topic, ()))
        if topic == RequestType.TRANSPORT and not _AIRPORT.search(turn.text):
            services.reverse()
        return any(self.try_start_service(turn, s) for s in services)

    # ============================================================ providers
    def resolve_provider(self, turn: _Turn, svc: TransactionService, service_type: str,
                         venue: Place | None) -> ExternalProvider | None:
        cap = turn.runtime.capabilities.service(service_type)
        if cap is None:
            return None
        if venue is not None:   # the venue's own reservation channel
            offering = turn.session.scalar(select(Offering).where(Offering.place_id == venue.id,
                                                                  Offering.service_type == service_type,
                                                                  Offering.active))
            if offering is None or offering.provider_id is None:
                return None
            provider = turn.session.get(ExternalProvider, offering.provider_id)
            return provider if provider is not None and provider.active else None
        return svc.provider_for(turn.runtime.property_id, cap.provider, service_type, turn.runtime.region)

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

    # ============================================================== drafting
    def try_start_service(self, turn: _Turn, service_type: str, *, preset: dict[str, Any] | None = None,
                          venue: Place | None = None) -> bool:
        spec = SERVICE_CATALOG[service_type]
        needs_venue = any(f.kind == "place_ref" and f.required for f in spec.fields)
        venue = venue or (self.resolve_venue(turn, service_type, turn.text) if needs_venue else None)
        if needs_venue and venue is None:
            return False   # e.g. "book a table" without a venue -> the property's own restaurant (staff)
        svc = TransactionService(turn.session, self.deps)
        provider = self.resolve_provider(turn, svc, service_type, venue)
        if provider is None:
            return False
        draft = {"service_type": service_type, "provider_id": provider.id, "values": {}, "partial": {},
                 "inferred": [], "asked": None}
        if venue is not None:
            draft["values"]["venue"], draft["values"]["_venue_id"] = venue.name, venue.id
        draft["values"].update(preset or {})
        self._absorb(turn, draft)
        log_event("transaction_draft_started", conversation_id=turn.conv.id, service_type=service_type,
                  provider=provider.slug)
        return self._advance(turn, svc, draft)

    def prepare(self, turn: _Turn, service_type: str, text: str, *, preset: dict[str, Any] | None = None,
                inferred: list[str] | None = None, venue: Place | None = None) -> PlannedOffer:
        """Prepare one service of a multi-part request without replying:
        an offer, or the details still missing (never guessed)."""
        svc = TransactionService(turn.session, self.deps)
        provider = self.resolve_provider(turn, svc, service_type, venue)
        if provider is None:
            return PlannedOffer(service_type, "unsupported")
        draft = {"service_type": service_type, "provider_id": provider.id, "values": {}, "partial": {},
                 "inferred": [], "asked": None}
        if venue is not None:
            draft["values"]["venue"], draft["values"]["_venue_id"] = venue.name, venue.id
        self._absorb(turn, draft, text=text)
        for key, value in (preset or {}).items():
            if key not in draft["values"] and key not in draft["partial"]:
                draft["values"][key] = value
                if key in (inferred or []):
                    draft["inferred"] = sorted(set(draft["inferred"]) | {key})
        self._fill_from_stay(turn, draft)
        missing = self._missing(draft)
        if missing:
            draft["asked"] = missing[0].key
            return PlannedOffer(service_type, "missing", provider=provider, missing=[f.key for f in missing],
                                draft=draft)
        return self.make_offer(turn, svc, provider, service_type, dict(draft["values"]))

    def question(self, turn: _Turn, draft: dict[str, Any]) -> str:
        spec_field = next(f for f in self._spec(draft).fields if f.key == draft["asked"])
        if spec_field.kind == "datetime" and draft["partial"].get(spec_field.key):
            day = date.fromisoformat(draft["partial"][spec_field.key])
            return msg.t("ask_time_on", turn.language, day=day.strftime("%d.%m.%Y"))
        key = f"ask_{spec_field.key}" if f"ask_{spec_field.key}" in msg.CATALOG else f"ask_{spec_field.kind}"
        if key not in msg.CATALOG:
            key = "ask_text"
        return msg.t(key, turn.language, field=spec_field.labels.get(turn.language.split("-")[0], spec_field.key))

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
            previous = datetime.fromisoformat(draft["values"][key]) if draft["values"].get(key) else None
            partial = date.fromisoformat(draft["partial"][key]) if draft["partial"].get(key) else None
            dt, pending = resolve_datetime(parts, previous, now_local, partial)
            if dt is not None:
                if draft["values"].get(key) != dt.isoformat():
                    draft["values"][key], changed = dt.isoformat(), True
                draft["partial"].pop(key, None)
            elif pending is not None:
                draft["partial"][key] = pending.isoformat()
                changed = True
        draft["inferred"] = sorted(set(draft["inferred"]) | ex.inferred)
        asked = draft.get("asked")
        non_answer = (turn.intent is not None and turn.intent.intent == Intent.GENERAL_CONVERSATION) \
            or fold(text).strip(" .!") in AFFIRMATIVE | NEGATIVE
        if not changed and asked and not non_answer:
            spec_field = next(f for f in spec.fields if f.key == asked)
            if spec_field.kind == "count" and re.fullmatch(r"\s*(\d{1,2})\s*", text):
                draft["values"][asked], changed = int(text.strip()), True
            elif spec_field.kind == "place_ref":
                venue = self.resolve_venue(turn, draft["service_type"], text)
                if venue is not None:
                    draft["values"]["venue"], draft["values"]["_venue_id"], changed = venue.name, venue.id, True
            elif (answer := free_text_answer(text, spec_field.kind)) is not None:
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

    def _missing(self, draft: dict[str, Any]) -> list[Any]:
        return [f for f in self._spec(draft).fields if f.required and f.key not in draft["values"]]

    def _advance(self, turn: _Turn, svc: TransactionService, draft: dict[str, Any]) -> bool:
        self._fill_from_stay(turn, draft)
        missing = self._missing(draft)
        if missing:
            draft["asked"] = missing[0].key
            turn.set_state(txn_draft=draft)
            turn.reply = self.question(turn, draft)
            turn.succeeded = True
            return True
        turn.set_state(txn_draft=None)
        provider = turn.session.get(ExternalProvider, draft["provider_id"])
        return self._offer(turn, svc, provider, draft["service_type"], dict(draft["values"]))

    def _draft_turn(self, turn: _Turn, svc: TransactionService, draft: dict[str, Any]) -> bool:
        if _hits(turn.text, _DRAFT_DROP) and len(turn.text.split()) <= 4:
            turn.set_state(txn_draft=None)
            turn.reply, turn.succeeded = msg.t("txn_draft_dropped", turn.language), True
            return True
        if self._absorb(turn, draft):
            return self._advance(turn, svc, draft)
        if turn.intent and turn.intent.intent in (Intent.UNKNOWN, Intent.GENERAL_CONVERSATION):
            return self._advance(turn, svc, draft)   # re-ask the missing detail
        return False   # the guest moved on; the draft waits

    # ================================================================ offers
    def make_offer(self, turn: _Turn, svc: TransactionService, provider: ExternalProvider, service_type: str,
                   details: dict[str, Any]) -> PlannedOffer:
        """Inventory check + provider quote, without replying."""
        try:
            quote = svc.request_quote(stay=turn.stay, conv=turn.conv, provider=provider, service_type=service_type,
                                      details=details, locale=turn.language)
        except NoAvailability as exc:
            return PlannedOffer(service_type, "unavailable", provider=provider,
                                detail=self._unavailable_text(turn, service_type, exc))
        except ProviderError as exc:
            log_event("quote_failed", provider=provider.slug, error=f"{exc.kind}: {exc}")
            return PlannedOffer(service_type, "failed", provider=provider)
        spec = SERVICE_CATALOG[service_type]
        when_key = next((f.key for f in spec.fields if f.kind == "datetime"), None)
        starts = datetime.fromisoformat(details[when_key]) if when_key and details.get(when_key) else None
        itinerary.link_quote(turn.session, stay_id=turn.stay.id, kind=DOMAIN_CATEGORY.get(spec.domain, "OTHER"),
                             title=self._summary(turn, quote), quote=quote, starts_at=starts)
        turn.offered_quote_ids.append(quote.id)
        turn.actions.append(ActionTaken(kind="quote", id=quote.id, detail={
            "service_type": service_type, "status": quote.status.value, "amount": str(quote.amount),
            "currency": quote.currency, "code": quote.code}))
        return PlannedOffer(service_type, "offered", quote=quote, provider=provider)

    def _offer(self, turn: _Turn, svc: TransactionService, provider: ExternalProvider, service_type: str,
               details: dict[str, Any], prefix: str = "") -> bool:
        planned = self.make_offer(turn, svc, provider, service_type, details)
        if planned.outcome == "unavailable":
            turn.reply = prefix + planned.detail
        elif planned.outcome == "failed":
            self.submit_staff(turn, "staff_question", turn.text,
                              f"Guest wants {service_type}, provider {provider.slug} quote failed: "
                              f"{ {k: v for k, v in details.items() if not k.startswith('_')} }")
            turn.reply = prefix + msg.t("quote_unavailable", turn.language, provider=provider.name)
        else:
            assert planned.quote is not None
            turn.reply = prefix + self._quote_text(turn, planned.quote, provider)
        turn.succeeded = True
        return True

    def _unavailable_text(self, turn: _Turn, service_type: str, exc: NoAvailability) -> str:
        tz = turn.runtime.timezone
        alts = []
        for m in exc.alternatives:
            title = (m.offering.title or {}).get(turn.language.split("-")[0]) or (m.offering.title or {}).get("en") \
                or m.offering.slug
            start = as_utc(m.slot.starts_at).astimezone(ZoneInfo(tz))
            alts.append(f"{title} {start.strftime('%d.%m %H:%M')}")
        alt_text = msg.t("alternatives", turn.language, list="; ".join(alts)) if alts else ""
        reason_key = f"reason_{exc.reason}"
        reason = msg.t(reason_key, turn.language) if reason_key in msg.CATALOG else exc.reason
        return msg.t("no_availability", turn.language, service=action_label(service_type, turn.language),
                     reason=reason, alternatives=alt_text)

    def _summary(self, turn: _Turn, quote: Quote, *, with_label: bool = True) -> str:
        text = format_summary(quote.service_type, {k: v for k, v in quote.request.items() if not k.startswith("_")},
                              turn.language, turn.runtime.timezone, turn.prop.name, with_label=with_label)
        if quote.request.get("offering") and quote.service_type not in VENUE_CATEGORIES:
            text += f"; {quote.request['offering']}"
        return text

    def _quote_text(self, turn: _Turn, quote: Quote, provider: ExternalProvider, key: str = "quote_offer") -> str:
        tz = turn.runtime.timezone
        return msg.t(
            key, turn.language, provider=provider.name, price=format_price(quote.amount, quote.currency),
            summary=self._summary(turn, quote), conditions=f"{quote.conditions} " if quote.conditions else "",
            valid_until=format_until(quote.valid_until, tz, self.deps.clock.now()), code=quote.code,
        )

    def offer_line(self, turn: _Turn, quote: Quote, *, with_label: bool = True) -> str:
        return f"{self._summary(turn, quote, with_label=with_label)} - " \
               f"{format_price(quote.amount, quote.currency)} ({quote.code})"

    def _modified(self, turn: _Turn, quote: Quote) -> dict[str, Any] | None:
        draft = {"service_type": quote.service_type, "values": dict(quote.request), "partial": {}, "inferred": [],
                 "asked": None}
        before = dict(draft["values"])
        if self._absorb(turn, draft) and draft["values"] != before:
            return draft["values"]
        return None

    # ====================================================== responding to offers
    def _quotes_turn(self, turn: _Turn, svc: TransactionService, quotes: list[Quote]) -> bool:
        awaiting_ids = set(turn.state.get("awaiting_quotes") or [])
        code = mentioned_code(turn.text)
        referenced = [q for q in quotes if q.code == code or _hits(turn.text, service_refs(q.service_type))]
        awaiting = [q for q in quotes if q.id in awaiting_ids]
        candidates = referenced or awaiting
        if not candidates:
            return False   # consent must refer to an offer the guest was just shown or names
        refs = [q.code for q in quotes] + [w for q in quotes for w in service_refs(q.service_type)]

        if len(candidates) == 1:
            quote = candidates[0]
            new_details = self._modified(turn, quote)
            if new_details is not None:
                provider = turn.session.get(ExternalProvider, quote.provider_id)
                return self._offer(turn, svc, provider, quote.service_type, new_details,
                                   prefix=msg.t("quote_updated", turn.language))
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
            provider = turn.session.get(ExternalProvider, quote.provider_id)
            turn.reply = self._quote_text(turn, quote, provider, key="quote_clarify")
            turn.offered_quote_ids.append(quote.id)
            turn.succeeded = True
            return True
        return False

    def _which_offer(self, turn: _Turn, quotes: list[Quote]) -> bool:
        turn.reply = msg.t("which_offer", turn.language,
                           offers="; ".join(self.offer_line(turn, q) for q in quotes), example=quotes[0].code)
        turn.offered_quote_ids.extend(q.id for q in quotes)
        turn.succeeded = True
        return True

    def _accept_all(self, turn: _Turn, svc: TransactionService, quotes: list[Quote]) -> bool:
        lines = []
        for quote in quotes:
            provider = turn.session.get(ExternalProvider, quote.provider_id)
            try:
                txn = svc.accept(quote, message_id=turn.guest_message_id, text=turn.text)
            except QuoteExpired:
                planned = self.make_offer(turn, svc, provider, quote.service_type, dict(quote.request))
                lines.append(msg.t("quote_expired", turn.language) + (
                    self._quote_text(turn, planned.quote, provider) if planned.quote else planned.detail))
                continue
            except NoAvailability as exc:
                lines.append(self._unavailable_text(turn, quote.service_type, exc))
                continue
            except QuoteNotOpen:
                continue
            itinerary.link_action(turn.session, quote.id, txn.action_id)
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
    def _post_confirmation_turn(self, turn: _Turn, svc: TransactionService,
                                active: list[ExternalTransaction]) -> bool:
        wants_cancel = _hits(turn.text, _CANCEL) and not _hits(turn.text, _POLICY)
        wants_change = _hits(turn.text, _CHANGE)
        if not (wants_cancel or wants_change):
            return False
        referenced = [t for t in active if _hits(turn.text, service_refs(t.action.action_type))]
        if len(referenced) == 1:
            txn = referenced[0]
        elif not referenced and len(active) == 1 and len(turn.text.split()) <= 4:
            txn = active[0]
        elif not referenced and len(active) == 1:
            return False   # not clearly about the booking
        else:
            turn.reply = msg.t("which_booking", turn.language, bookings="; ".join(
                f"{action_label(t.action.action_type, turn.language)} ({t.provider.name})"
                for t in (referenced or active)))
            turn.succeeded = True
            return True
        if wants_cancel:
            svc.request_cancel(txn)
            if txn.action.status.value == "cancelled":
                turn.reply = self._report_line(turn, svc, txn)
            else:
                turn.reply = msg.t("txn_cancel_requested", turn.language, provider=txn.provider.name,
                                   service=action_label(txn.action.action_type, turn.language))
            turn.succeeded = True
            return True
        self.submit_staff(turn, "staff_question", turn.text,
                          f"Guest asks to change confirmed {txn.action.action_type} "
                          f"(ref {txn.provider_reference or txn.id}): {turn.text}")
        turn.reply = msg.t("txn_change_after_confirm", turn.language, provider=txn.provider.name,
                           service=action_label(txn.action.action_type, turn.language))
        turn.succeeded = True
        return True
