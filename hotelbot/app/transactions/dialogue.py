"""Conversational front of the transaction layer.

Owns three dialogue situations, in this order:

1. An open quote the guest is responding to  -> consent / decline / modify / clarify
2. A draft request still missing details     -> ask only for what is missing
3. A confirmed transaction                   -> cancel / change-after-confirmation
4. A new request for a service the property offers through a provider

Rules that make it safe:
* Consent counts only for the quote the bot just offered (or the guest names
  its code), only if the whole message is an explicit affirmative, and only
  if the quote has not expired. Changed details => new quote, never consent.
* The bot never fills a missing detail by guessing; contextual inferences are
  shown in the quote the guest must accept.
* Everything said about a booking afterwards comes from stored state.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.actions.catalog import action_label
from app.agent import messages as msg
from app.agent.authority import transaction_status_message
from app.agent.intents import Intent
from app.agent.policies import AFFIRMATIVE, NEGATIVE
from app.db.models import Action, ExternalProvider, ExternalTransaction, Quote, RequestType
from app.observability import log_event
from app.schemas.messages import ActionTaken
from app.text import contains_phrase, fold
from app.transactions.catalog import SERVICE_CATALOG, TOPIC_SERVICES, ServiceSpec
from app.transactions.consent import ConsentDecision, classify, mentioned_code
from app.transactions.format import format_price, format_summary, format_until
from app.transactions.providers.base import ProviderError
from app.transactions.service import QuoteExpired, QuoteNotOpen, TransactionService, TxnDeps
from app.transactions.slots import extract, free_text_answer, local_now, resolve_datetime

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
_SERVICE_WORDS = {
    "transport": ["taxi", "transfer", "pickup", "pick-up", "driver", "ride", "cab", "car", "taksi", "prevoz",
                  "vozac", "такси", "трансфер", "водител*", "машин*"],
}


def _hits(text: str, phrases: list[str]) -> bool:
    f = fold(text)
    return any(contains_phrase(f, p) for p in phrases)


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
        quote = svc.open_quote(turn.conv.id)
        if quote is not None and self._quote_turn(turn, svc, quote):
            return True
        draft = turn.state.get("txn_draft")
        if draft and self._draft_turn(turn, svc, draft):
            return True
        active = svc.active_for_stay(turn.stay.id)
        if active and self._post_confirmation_turn(turn, svc, active[0]):
            return True
        if intent.intent in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST) and intent.request_type:
            return self.try_start(turn, intent.request_type)
        return False

    def try_start(self, turn: _Turn, topic: RequestType) -> bool:
        """Start a transaction if the property offers a matching service
        through an active provider."""
        services = list(TOPIC_SERVICES.get(topic, ()))
        if not services:
            return False
        if topic == RequestType.TRANSPORT and not _AIRPORT.search(turn.text):
            services.reverse()   # a plain taxi request prefers "taxi"
        svc = TransactionService(turn.session, self.deps)
        for service_type in services:
            cap = turn.runtime.capabilities.service(service_type)
            if cap is None:
                continue
            provider = svc.provider_for(turn.runtime.property_id, cap.provider, service_type)
            if provider is None:
                continue
            draft = {"service_type": service_type, "provider_id": provider.id, "values": {}, "partial": {},
                     "inferred": [], "asked": None}
            self._absorb(turn, draft)
            log_event("transaction_draft_started", conversation_id=turn.conv.id, service_type=service_type,
                      provider=provider.slug)
            return self._advance(turn, svc, draft)
        return False

    # ============================================================== drafting
    def _spec(self, draft: dict[str, Any]) -> ServiceSpec:
        return SERVICE_CATALOG[draft["service_type"]]

    def _now_local(self, turn: _Turn) -> datetime:
        return local_now(self.deps.clock.now(), turn.runtime.timezone)

    def _absorb(self, turn: _Turn, draft: dict[str, Any]) -> bool:
        """Merge details from the guest's message into the draft."""
        spec = self._spec(draft)
        now_local = self._now_local(turn)
        ex = extract(turn.text, spec, now_local.date())
        changed = False
        for key, value in ex.values.items():
            if draft["values"].get(key) != value:
                draft["values"][key], changed = value, True
        for key, parts in ex.when.items():
            previous = datetime.fromisoformat(draft["values"][key]) if draft["values"].get(key) else None
            partial = date.fromisoformat(draft["partial"][key]) if draft["partial"].get(key) else None
            dt, pending = resolve_datetime(parts, previous, now_local, partial)
            if dt is not None:
                draft["values"][key] = dt.isoformat()
                draft["partial"].pop(key, None)
                changed = True
            elif pending is not None:
                draft["partial"][key] = pending.isoformat()
                changed = True
        draft["inferred"] = sorted(set(draft["inferred"]) | ex.inferred)
        asked = draft.get("asked")
        non_answer = (turn.intent is not None and turn.intent.intent == Intent.GENERAL_CONVERSATION) \
            or fold(turn.text).strip(" .!") in AFFIRMATIVE | NEGATIVE
        if not changed and asked and not non_answer:
            field = next(f for f in spec.fields if f.key == asked)
            if field.kind == "count" and re.fullmatch(r"\s*(\d{1,2})\s*", turn.text):
                draft["values"][asked], changed = int(turn.text.strip()), True
            elif (answer := free_text_answer(turn.text, field.kind)) is not None:
                draft["values"][asked], changed = answer, True
        return changed

    def _fill_from_stay(self, turn: _Turn, draft: dict[str, Any]) -> None:
        """Party size the guest already told us (or staff verified) - shown
        in the quote, so the guest confirms it with the price."""
        for field in self._spec(draft).fields:
            if field.kind == "count" and field.key not in draft["values"]:
                verified = turn.stay.party_size
                stated = (turn.stay.facts or {}).get("guest_count", {}).get("value")
                if verified or stated:
                    draft["values"][field.key] = verified or stated

    def _advance(self, turn: _Turn, svc: TransactionService, draft: dict[str, Any]) -> bool:
        spec = self._spec(draft)
        self._fill_from_stay(turn, draft)
        missing = [f for f in spec.fields if f.required and f.key not in draft["values"]]
        if missing:
            field = missing[0]
            draft["asked"] = field.key
            turn.set_state(txn_draft=draft)
            key = f"ask_{field.key}" if f"ask_{field.key}" in msg.CATALOG else f"ask_{field.kind}"
            turn.reply = msg.t(key, turn.language, field=field.labels.get(turn.language.split("-")[0], field.key))
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

    # ================================================================ quotes
    def _offer(self, turn: _Turn, svc: TransactionService, provider: ExternalProvider, service_type: str,
               details: dict[str, Any], prefix: str = "") -> bool:
        try:
            quote = svc.request_quote(stay=turn.stay, conv=turn.conv, provider=provider, service_type=service_type,
                                      details=details, locale=turn.language)
        except ProviderError as exc:
            log_event("quote_failed", provider=provider.slug, error=f"{exc.kind}: {exc}")
            self.submit_staff(turn, "staff_question", turn.text,
                              f"Guest wants {service_type}, provider {provider.slug} quote failed: {details}")
            turn.reply = prefix + msg.t("quote_unavailable", turn.language, provider=provider.name)
            turn.succeeded = True
            return True
        turn.reply = prefix + self._quote_text(turn, quote, provider)
        turn.offered_quote_id = quote.id
        turn.actions.append(ActionTaken(kind="quote", id=quote.id, detail={
            "service_type": service_type, "status": quote.status.value, "amount": str(quote.amount),
            "currency": quote.currency, "code": quote.code}))
        turn.succeeded = True
        return True

    def _quote_text(self, turn: _Turn, quote: Quote, provider: ExternalProvider, key: str = "quote_offer") -> str:
        tz = turn.runtime.timezone
        return msg.t(
            key, turn.language, provider=provider.name, price=format_price(quote.amount, quote.currency),
            summary=format_summary(quote.service_type, quote.request, turn.language, tz, turn.prop.name),
            conditions=f"{quote.conditions} " if quote.conditions else "",
            valid_until=format_until(quote.valid_until, tz, self.deps.clock.now()), code=quote.code,
        )

    def _modified(self, turn: _Turn, quote: Quote) -> dict[str, Any] | None:
        """New details if the message changes the quoted ones, else None."""
        draft = {"service_type": quote.service_type, "values": dict(quote.request), "partial": {}, "inferred": [],
                 "asked": None}
        before = dict(draft["values"])
        if self._absorb(turn, draft) and draft["values"] != before:
            return draft["values"]
        return None

    def _quote_turn(self, turn: _Turn, svc: TransactionService, quote: Quote) -> bool:
        scoped = turn.state.get("awaiting_quote") == quote.id or mentioned_code(turn.text) == quote.code
        if not scoped:
            return False   # consent must refer to this offer
        provider = turn.session.get(ExternalProvider, quote.provider_id)
        new_details = self._modified(turn, quote)
        decision = classify(turn.text, quote.code, new_details is not None)
        log_event("consent_decision", quote_id=quote.id, decision=decision.value)

        if decision == ConsentDecision.MODIFY:
            assert new_details is not None
            return self._offer(turn, svc, provider, quote.service_type, new_details,
                               prefix=msg.t("quote_updated", turn.language))
        if decision == ConsentDecision.DECLINE:
            svc.decline(quote)
            turn.reply, turn.succeeded = msg.t("quote_declined", turn.language), True
            return True
        if decision == ConsentDecision.CONSENT:
            try:
                txn = svc.accept(quote, message_id=turn.guest_message_id, text=turn.text)
            except QuoteExpired:
                return self._offer(turn, svc, provider, quote.service_type, dict(quote.request),
                                   prefix=msg.t("quote_expired", turn.language))
            except QuoteNotOpen:
                return False
            self._report(turn, svc, txn)
            return True
        # NONE: questions about the offer or hesitation -> restate, still nothing booked
        intent = turn.intent.intent if turn.intent else Intent.UNKNOWN
        if intent in (Intent.UNKNOWN, Intent.GENERAL_CONVERSATION) or _hits(turn.text, _AMBIGUOUS):
            turn.reply = self._quote_text(turn, quote, provider, key="quote_clarify")
            turn.offered_quote_id = quote.id
            turn.succeeded = True
            return True
        return False

    # ====================================================== after consent
    def _report(self, turn: _Turn, svc: TransactionService, txn: ExternalTransaction) -> None:
        action = txn.action
        turn.reply = transaction_status_message(svc.view(txn, action, turn.prop, turn.language), turn.language)
        turn.actions.append(ActionTaken(kind="transaction", id=action.id, detail={
            "action_type": action.action_type, "status": action.status.value, "quote_id": txn.quote_id,
            "transaction_id": txn.id}))
        turn.succeeded = True

    def _post_confirmation_turn(self, turn: _Turn, svc: TransactionService, txn: ExternalTransaction) -> bool:
        domain = SERVICE_CATALOG[txn.action.action_type].domain
        about_service = _hits(turn.text, _SERVICE_WORDS.get(domain, [])) or len(turn.text.split()) <= 3
        if _hits(turn.text, _CANCEL) and about_service and not _hits(turn.text, _POLICY):
            svc.request_cancel(txn)
            if txn.action.status.value == "cancelled":
                self._report(turn, svc, txn)
            else:
                turn.reply = msg.t("txn_cancel_requested", turn.language, provider=txn.provider.name,
                                   service=action_label(txn.action.action_type, turn.language))
                turn.succeeded = True
            return True
        if _hits(turn.text, _CHANGE) and about_service:
            self.submit_staff(turn, "staff_question", turn.text,
                              f"Guest asks to change confirmed {txn.action.action_type} "
                              f"(ref {txn.provider_reference or txn.id}): {turn.text}")
            turn.reply = msg.t("txn_change_after_confirm", turn.language, provider=txn.provider.name,
                               service=action_label(txn.action.action_type, turn.language))
            turn.succeeded = True
            return True
        return False
