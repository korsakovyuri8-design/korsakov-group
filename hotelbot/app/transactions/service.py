"""External Service Transaction Layer - domain operations.

    request intent -> capability -> provider discovery -> QUOTE (offered)
        -> explicit guest consent to THIS quote -> Action PROPOSED + ExternalTransaction
        -> job: provider_submit (idempotency key) -> SUBMITTED -> ACCEPTED | REJECTED
        -> provider callbacks / staff -> IN_PROGRESS -> COMPLETED | FAILED | CANCELLED

Invariants:
* quote != booking; submitted != accepted; accepted != completed.
* Every status change goes through ActionService (validated + audited).
* Every guest-facing statement about a transaction is derived from stored
  state (app/agent/authority.py) and delivered through the job queue.
* Every provider call that can create an obligation carries an idempotency
  key; retries can never create a second booking.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.actions.executors import ExecutorRegistry
from app.actions.service import ActionService, InvalidTransition
from app.agent.handoff import create_handoff
from app.agent.policies import HandoffDecision, HandoffReason
from app.clock import Clock, as_utc
from app.db.models import (
    Action,
    ActionEvent,
    ActionStatus,
    Conversation,
    ExternalProvider,
    ExternalTransaction,
    MessageRole,
    Property,
    Quote,
    QuoteStatus,
    Stay,
    Urgency,
)
from app.db.repositories import ConversationRepository
from app.jobs.queue import PermanentJobError, RetryableJobError, enqueue
from app.observability import log_event
from app.marketplace import pricing
from app.marketplace import inventory as availability
from app.transactions.format import format_price, format_summary
from app.transactions.providers.base import ProviderError, ProviderTimeout, QuoteRequest, SubmitRequest
from app.transactions.providers.registry import ProviderRegistry
from app.whatsapp.base import MessageTransport

if TYPE_CHECKING:
    from app.marketplace.discovery import Candidate

S = ActionStatus
CANCELLABLE = (S.PROPOSED, S.SUBMITTED, S.PENDING_CONDITION, S.ACCEPTED)
ACTIVE = (S.PROPOSED, S.SUBMISSION_UNKNOWN, S.SUBMITTED, S.PENDING_CONDITION, S.ACCEPTED, S.IN_PROGRESS)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # no I/O: not confusable with digits

_OUTCOME_STATUS = {
    "accepted": S.ACCEPTED, "accepted_conditional": S.PENDING_CONDITION, "rejected": S.REJECTED, "in_progress": S.IN_PROGRESS, "completed": S.COMPLETED,
    "failed": S.FAILED, "cancelled": S.CANCELLED,
}


class QuoteNotOpen(Exception):
    pass


class QuoteExpired(Exception):
    pass


@dataclass
class TxnDeps:
    clock: Clock
    providers: ProviderRegistry
    executors: ExecutorRegistry
    transport_for: Callable[[str], MessageTransport]
    handoff_context: int = 10


class TransactionService:
    def __init__(self, session: Session, deps: TxnDeps) -> None:
        self.s = session
        self.deps = deps
        self.clock = deps.clock

    # ------------------------------------------------------------ discovery
    def provider_for(self, property_id: str, declared_provider: str | None, service_type: str,
                     region: str | None = None) -> ExternalProvider | None:
        """The declared provider if it is active and offers the service,
        otherwise the first active provider offering it - the property's own
        partners first, then region-scoped providers."""
        scope = ExternalProvider.property_id == property_id
        if region:
            scope = or_(scope, ExternalProvider.region == region)
        rows = self.s.scalars(select(ExternalProvider).where(scope, ExternalProvider.active)
                              .order_by(ExternalProvider.region.is_not(None), ExternalProvider.slug))
        candidates = [p for p in rows if service_type in (p.services or {}).get("service_types", [])]
        if declared_provider:
            candidates = [p for p in candidates if p.slug == declared_provider]
        return candidates[0] if candidates else None

    # ---------------------------------------------------------------- quotes
    def request_quote(self, *, stay: Stay, conv: Conversation, provider: ExternalProvider, service_type: str,
                      details: dict[str, Any], locale: str, candidate: Candidate | None = None,
                      supersede: bool = True, replaces: ExternalTransaction | None = None) -> Quote:
        """Hold the inventory the candidate needs, then ask the provider for a
        price. The hold expires with the quote. Raises NoAvailability (the
        last unit went to someone else a moment ago) or ProviderError."""
        details = dict(details)
        offering = candidate.offering if candidate else None
        if candidate is not None:
            details.update(candidate.resolved)
        if offering is not None:
            details["_offering_id"] = offering.id
            details["offering"] = (offering.title or {}).get("en") or offering.slug
            if (offering.attributes or {}).get("weather_dependent"):
                details["weather_dependent"] = True
        now = self.clock.now()
        validity = timedelta(minutes=int((provider.config or {}).get("validity_minutes", 15)))
        holds = []
        if candidate is not None and offering is not None and candidate.start is not None and candidate.need:
            holds = availability.hold(self.s, offering, candidate.start, candidate.end or candidate.start,
                                      candidate.need, now=now, expires_at=now + validity,
                                      ignore_transaction=replaces.id if replaces else None)
        provider_details = {k: v for k, v in details.items() if not k.startswith("_")}
        offering_info = None
        if offering is not None:
            offering_info = {"slug": offering.slug, "title": (offering.title or {}).get("en"),
                             "pricing": offering.pricing, "currency": offering.currency,
                             "attributes": offering.attributes}
        try:
            result = self.deps.providers.adapter(provider).request_quote(
                QuoteRequest(service_type, provider_details, locale, offering_info))
        except ProviderError:
            for h in holds:
                self.s.delete(h)
            raise
        if supersede:
            self.supersede_open(conv.id, service_type)
        terms = dict(candidate.terms) if candidate else {}
        quote = Quote(
            code="".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)),
            property_id=stay.property_id, stay_id=stay.id, guest_id=stay.guest_id, conversation_id=conv.id,
            provider_id=provider.id, service_type=service_type, request=details, currency=result.currency,
            amount=result.amount, description=result.description, conditions=result.conditions,
            valid_until=result.valid_until, provider_reference=result.provider_reference, status=QuoteStatus.OFFERED,
            consent={}, terms=terms, offering_id=offering.id if offering else None,
            commercial=pricing.commission(offering or provider, result.amount),
            replaces_transaction_id=replaces.id if replaces else None, created_at=now,
        )
        self.s.add(quote)
        self.s.flush()
        for h in holds:
            h.quote_id, h.expires_at = quote.id, as_utc(quote.valid_until)
        log_event("quote_offered", quote_id=quote.id, provider=provider.slug, service_type=service_type,
                  amount=str(quote.amount), currency=quote.currency, offering=offering.slug if offering else None,
                  held=sum(h.quantity for h in holds))
        return quote

    def open_quotes(self, conversation_id: str) -> list[Quote]:
        return list(self.s.scalars(select(Quote).where(Quote.conversation_id == conversation_id,
                                                       Quote.status == QuoteStatus.OFFERED)
                                   .order_by(Quote.created_at.desc())))

    def open_quote(self, conversation_id: str) -> Quote | None:
        return self.s.scalar(select(Quote).where(Quote.conversation_id == conversation_id,
                                                 Quote.status == QuoteStatus.OFFERED)
                             .order_by(Quote.created_at.desc()).limit(1))

    def supersede_open(self, conversation_id: str, service_type: str | None = None) -> None:
        """A new offer replaces open offers for the SAME service only; a trip
        plan may hold several open offers (transfer, skis, guide) at once."""
        q = select(Quote).where(Quote.conversation_id == conversation_id, Quote.status == QuoteStatus.OFFERED)
        if service_type:
            q = q.where(Quote.service_type == service_type)
        for quote in self.s.scalars(q):
            quote.status, quote.decided_at = QuoteStatus.SUPERSEDED, self.clock.now()
            availability.release_quote(self.s, quote.id)

    def is_expired(self, quote: Quote) -> bool:
        return as_utc(quote.valid_until) <= self.clock.now()

    def expire(self, quote: Quote) -> None:
        if quote.status == QuoteStatus.OFFERED:
            quote.status, quote.decided_at = QuoteStatus.EXPIRED, self.clock.now()
            availability.release_quote(self.s, quote.id)
            log_event("quote_expired", quote_id=quote.id)

    def decline(self, quote: Quote) -> None:
        quote.status, quote.decided_at = QuoteStatus.DECLINED_BY_GUEST, self.clock.now()
        availability.release_quote(self.s, quote.id)
        log_event("quote_declined", quote_id=quote.id)

    def accept(self, quote: Quote, *, message_id: str | None, text: str) -> ExternalTransaction:
        """Record explicit consent and create the transaction. The provider is
        contacted asynchronously by the provider_submit job."""
        if quote.status != QuoteStatus.OFFERED:
            raise QuoteNotOpen(quote.status.value)
        if self.is_expired(quote):
            self.expire(quote)
            raise QuoteExpired(quote.id)
        quote.status, quote.decided_at = QuoteStatus.ACCEPTED_BY_GUEST, self.clock.now()
        quote.consent = {"message_id": message_id, "text": text, "at": self.clock.now().isoformat(),
                         "amount": str(quote.amount), "currency": quote.currency, "code": quote.code,
                         "terms": quote.terms or {}}
        action = ActionService(self.s, self.deps.executors).propose(
            property_id=quote.property_id, stay_id=quote.stay_id, conversation_id=quote.conversation_id,
            action_type=quote.service_type, executor="provider",
            summary=f"{quote.service_type} via provider, {format_price(quote.amount, quote.currency)}",
            params={"quote_id": quote.id, "details": quote.request, "amount": str(quote.amount),
                    "currency": quote.currency, "replaces": quote.replaces_transaction_id},
        )
        txn = ExternalTransaction(action_id=action.id, provider_id=quote.provider_id, quote_id=quote.id,
                                  idempotency_key=f"txn-{quote.id}",
                                  request={k: v for k, v in quote.request.items() if not k.startswith("_")},
                                  submit_attempts=0,
                                  created_at=self.clock.now())
        self.s.add(txn)
        self.s.flush()
        if not availability.confirm(self.s, quote.id, txn.id, self.clock.now()):
            raise availability.NoAvailability("slot_gone")
        enqueue(self.s, "provider_submit", {"transaction_id": txn.id}, f"submit:{txn.id}", clock=self.clock)
        log_event("quote_accepted", quote_id=quote.id, transaction_id=txn.id, action_id=action.id)
        return txn

    # ---------------------------------------------------------- transactions
    def for_action(self, action_id: str) -> ExternalTransaction | None:
        return self.s.scalar(select(ExternalTransaction).where(ExternalTransaction.action_id == action_id))

    def active_for_stay(self, stay_id: str) -> list[ExternalTransaction]:
        rows = self.s.scalars(select(ExternalTransaction).join(Action, ExternalTransaction.action_id == Action.id)
                              .where(Action.stay_id == stay_id, Action.status.in_(ACTIVE))
                              .order_by(ExternalTransaction.created_at.desc()))
        return list(rows)

    def request_cancel(self, txn: ExternalTransaction, template: str | None = None) -> bool:
        if txn.action.status not in CANCELLABLE:
            return False
        if txn.action.status == S.PROPOSED:
            # Not yet sent: cancel locally; the pending submit job becomes a no-op.
            self.apply_status(txn, S.CANCELLED, "guest", "cancelled before submission", template)
        else:
            enqueue(self.s, "provider_cancel", {"transaction_id": txn.id, "template": template},
                    f"cancel:{txn.id}", clock=self.clock)
        return True

    def cancellation_policy(self, txn: ExternalTransaction) -> CancelPolicy:
        """What cancelling now means under the provider's stated policy -
        checked BEFORE promising anything to the guest."""
        terms = txn.quote.terms or {}
        hours = terms.get("free_cancellation_hours")
        start = _start_of(txn.request)
        if hours is None or start is None:
            return CancelPolicy("unknown", None, terms.get("cancellation_fee"))
        deadline = start - timedelta(hours=float(hours))
        if self.clock.now() <= deadline:
            return CancelPolicy("free", deadline, None)
        return CancelPolicy("fee", deadline, terms.get("cancellation_fee") or "per the provider's policy")

    def apply_status(self, txn: ExternalTransaction, target: ActionStatus, actor: str,
                     detail: str | None = None, template: str | None = None) -> str:
        """Move the transaction's action through the state machine; queue the
        guest notification; escalate failures to staff."""
        action = txn.action
        if action.status == target:
            return "already_in_state"
        try:
            ActionService(self.s, self.deps.executors).transition(action, target, actor, detail)
        except InvalidTransition:
            log_event("transaction_invalid_transition", transaction_id=txn.id, current=action.status.value,
                      target=target.value, actor=actor)
            return "invalid_transition"
        self.queue_notification(action, target, template)
        if target in (S.REJECTED, S.CANCELLED, S.FAILED):
            availability.release_transaction(self.s, txn.id)
        replaces = (action.params or {}).get("replaces")
        if target == S.ACCEPTED and replaces and not (action.params or {}).get("modified_in_place"):
            # The new booking is confirmed: only now is the old one cancelled,
            # so a failed change never leaves the guest with nothing.
            old = self.s.get(ExternalTransaction, replaces)
            if old is not None:
                self.request_cancel(old, template="txn_replaced")
        if target == S.FAILED:
            self._escalate(txn, detail)
        return "applied"

    def queue_notification(self, action: Action, status: ActionStatus, template: str | None = None) -> None:
        key = f"notify:{action.id}:{template or status.value}"
        enqueue(self.s, "notify_guest", {"action_id": action.id, "status": status.value, "template": template},
                key, clock=self.clock)

    def _escalate(self, txn: ExternalTransaction, detail: str | None) -> None:
        conv = self.s.get(Conversation, txn.action.conversation_id) if txn.action.conversation_id else None
        if conv is None:
            return
        create_handoff(self.s, conv, HandoffDecision(HandoffReason.PROVIDER_FAILURE, Urgency.HIGH, "txn_failed"),
                       f"[system] {txn.action.action_type} via provider failed: {detail or 'unknown error'}",
                       "TRANSACTION", self.deps.handoff_context)

    # ---------------------------------------------------------- job handlers
    def run_submit(self, transaction_id: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is None:
            raise PermanentJobError(f"unknown transaction {transaction_id}")
        if txn.action.status != S.PROPOSED:
            return  # already submitted, or cancelled before submission
        quote = txn.quote
        adapter = self.deps.providers.adapter(txn.provider)
        old = self.s.get(ExternalTransaction, quote.replaces_transaction_id) if quote.replaces_transaction_id else None
        modify = (old is not None and old.provider_id == txn.provider_id and old.provider_reference
                  and (txn.provider.config or {}).get("supports_modification") and hasattr(adapter, "modify")
                  and old.action.status in CANCELLABLE)
        request = SubmitRequest(
            idempotency_key=txn.idempotency_key, service_type=quote.service_type, details=txn.request,
            quote_reference=quote.provider_reference, amount=quote.amount, currency=quote.currency,
            customer_reference=txn.action.stay_id or txn.id,
            offering_ref=quote.request.get("_offering_id"),
            modifies_reference=old.provider_reference if modify and old else None,
        )
        try:
            outcome = adapter.modify(request) if modify else adapter.submit(request)
        except ProviderTimeout as exc:
            # timeout != failure: the provider may have created the booking.
            if not (txn.provider.config or {}).get("idempotent_submit", True):
                # No idempotency at the provider: a retry could book twice. Stop.
                self.mark_unknown(txn, f"timeout: {exc}")
                return
            raise RetryableJobError(f"timeout: {exc}") from exc   # same key: safe to retry
        except ProviderError as exc:
            raise (RetryableJobError if exc.retryable else PermanentJobError)(f"{exc.kind}: {exc}") from exc
        self.record_outcome(txn, outcome, modify=bool(modify), old=old)

    def record_outcome(self, txn: ExternalTransaction, outcome: Any, *, modify: bool = False,
                       old: ExternalTransaction | None = None) -> None:
        """Apply what the provider said about a submission (submit response
        or a reconciliation lookup)."""
        actor = f"provider:{txn.provider.slug}"
        if modify and old is not None and outcome.status == "accepted":
            # Modified in place: the old record ends as replaced, no cancellation call.
            txn.action.params = {**(txn.action.params or {}), "modified_in_place": True}
            self.apply_status(old, S.CANCELLED, actor, "replaced by a modification", template="txn_replaced")
        txn.provider_reference = outcome.reference or txn.provider_reference
        txn.submitted_at = self.clock.now()
        if txn.action.status != S.SUBMITTED:
            ActionService(self.s, self.deps.executors).transition(txn.action, S.SUBMITTED, actor)
        txn.action.external_ref = txn.provider_reference
        final = _OUTCOME_STATUS.get(outcome.status)
        if final is None:   # "received": stays SUBMITTED until the provider calls back
            self.queue_notification(txn.action, S.SUBMITTED)
        else:
            self.apply_status(txn, final, actor, outcome.message)
        log_event("transaction_submitted", transaction_id=txn.id, provider=txn.provider.slug,
                  outcome=outcome.status, reference=txn.provider_reference)

    def on_submit_dead(self, transaction_id: str, error: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is None or txn.action.status not in (S.PROPOSED, S.SUBMITTED):
            return
        txn.last_error = error
        txn.action.error = error
        if txn.action.status == S.PROPOSED and _ambiguous(error):
            # The last attempt may have reached the provider: never call it failed.
            self.mark_unknown(txn, error)
            return
        self.apply_status(txn, S.FAILED, "system:retries_exhausted", error)

    def mark_unknown(self, txn: ExternalTransaction, error: str) -> None:
        """SUBMISSION_UNKNOWN: tell the guest the truth (we don't know yet),
        ask staff to reconcile, and reconcile automatically if the provider
        can look a booking up by our idempotency key."""
        txn.last_error = txn.action.error = error
        ActionService(self.s, self.deps.executors).transition(txn.action, S.SUBMISSION_UNKNOWN,
                                                               "system:no_response", error)
        self.queue_notification(txn.action, S.SUBMISSION_UNKNOWN)
        self._escalate(txn, f"RECONCILE: no answer from the provider ({error}). The booking may exist - check "
                            f"with {txn.provider.name} (idempotency key {txn.idempotency_key}) before rebooking.")
        if (txn.provider.config or {}).get("supports_lookup"):
            enqueue(self.s, "provider_reconcile", {"transaction_id": txn.id}, f"reconcile:{txn.id}",
                    clock=self.clock, delay=timedelta(minutes=1))
        log_event("transaction_submission_unknown", transaction_id=txn.id, provider=txn.provider.slug, error=error)

    def run_reconcile(self, transaction_id: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is None or txn.action.status != S.SUBMISSION_UNKNOWN:
            return   # already settled (by staff or a callback)
        adapter = self.deps.providers.adapter(txn.provider)
        try:
            outcome = adapter.lookup(txn.idempotency_key)
        except ProviderError as exc:
            raise RetryableJobError(f"{exc.kind}: {exc}") from exc
        if outcome is None:
            log_event("reconcile_not_found", transaction_id=txn.id)
            return   # no booking found: staff decide (a lookup miss is not proof for every provider)
        self.record_outcome(txn, outcome)
        log_event("transaction_reconciled", transaction_id=txn.id, outcome=outcome.status)

    def run_cancel(self, transaction_id: str, template: str | None = None) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is None or txn.action.status not in CANCELLABLE or not txn.provider_reference:
            return
        try:
            outcome = self.deps.providers.adapter(txn.provider).cancel(txn.provider_reference, f"cancel-{txn.id}")
        except ProviderError as exc:
            raise (RetryableJobError if exc.retryable else PermanentJobError)(f"{exc.kind}: {exc}") from exc
        if outcome.status == "cancelled":
            self.apply_status(txn, S.CANCELLED, f"provider:{txn.provider.slug}", outcome.message, template)
        else:
            self.queue_notification(txn.action, txn.action.status, template="txn_cancel_refused")
            self._escalate(txn, f"cancellation refused: {outcome.message}")

    def on_cancel_dead(self, transaction_id: str, error: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is not None:
            self.queue_notification(txn.action, txn.action.status, template="txn_cancel_refused")
            self._escalate(txn, f"cancellation could not be delivered: {error}")

    def run_notify(self, action_id: str, status: str, template: str | None) -> None:
        from app.agent.authority import transaction_status_message

        action = self.s.get(Action, action_id)
        if action is None or action.conversation_id is None:
            return
        if template is None and action.status.value != status:
            return  # superseded by a newer state; that state has its own notification
        conv = self.s.get(Conversation, action.conversation_id)
        prop = self.s.get(Property, action.property_id)
        txn = self.for_action(action.id)
        assert conv is not None and prop is not None
        locale = conv.language or prop.default_language
        text = transaction_status_message(self.view(txn, action, prop, locale), locale, template)
        ConversationRepository(self.s).add_message(
            conv, MessageRole.BOT, text, language=conv.language,
            extra={"action_id": action.id, "action_status": action.status.value, "kind": "status_notification"})
        result = self.deps.transport_for(conv.channel).send_text(conv.guest.external_id, text)
        if not result.ok:
            raise RetryableJobError(f"delivery failed: {result.error}")
        log_event("message_sent", channel=conv.channel, kind="status_notification", action_id=action.id,
                  status=action.status.value)

    # ------------------------------------------------------------------ views
    def view(self, txn: ExternalTransaction | None, action: Action, prop: Property, locale: str) -> TxnView:
        was_accepted = self.s.scalar(select(ActionEvent.id).where(
            ActionEvent.action_id == action.id, ActionEvent.to_status == S.ACCEPTED).limit(1)) is not None
        provider_name = txn.provider.name if txn else prop.name
        details = {k: v for k, v in (txn.request if txn else {}).items() if k != "weather_dependent"}
        if action.action_type == "rental" and details.get("category"):
            from app.transactions.catalog import rental_label

            details["category"] = rental_label(details["category"], locale)
        return TxnView(
            status=action.status,
            service_type=action.action_type,
            provider_name=provider_name,
            summary=format_summary(action.action_type, details, locale, prop.timezone, prop.name,
                                   with_label=False) if txn else "",
            reference=(txn.provider_reference if txn else None) or "-",
            was_accepted=was_accepted,
        )


def _ambiguous(error: str) -> bool:
    """Errors after which the provider may have acted: timeouts and our own
    crashes. (5xx / connection refused are treated as 'not processed'.)"""
    return error.startswith("timeout") or error.split(":")[0] not in ("unavailable", "invalid_request", "auth")


@dataclass(frozen=True)
class CancelPolicy:
    status: str                    # free | fee | unknown
    deadline: datetime | None
    fee: str | None


def _start_of(details: dict[str, Any]) -> datetime | None:
    for key in ("pickup_time", "start_time", "reservation_time", "delivery_time"):
        if details.get(key):
            return as_utc(datetime.fromisoformat(details[key]))
    return None


@dataclass(frozen=True)
class TxnView:
    status: ActionStatus
    service_type: str
    provider_name: str
    summary: str
    reference: str
    was_accepted: bool
