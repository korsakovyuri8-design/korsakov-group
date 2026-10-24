"""Bridge from an Atlas Trip plan to the existing marketplace/transaction engine.

Compare:
    reads provider evidence; creates NO quote and NO booking.

Quote:
    asks a provider for a priced offer; creates a Quote only.

Booking:
    deliberately NOT implemented here. Explicit consent is a later step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.executors import default_executors
from app.clock import FrozenClock
from app.db.models import (
    ActionStatus,
    ExternalProvider,
    ExternalTransaction,
    Job,
    JobStatus,
    Quote,
    QuoteStatus,
    TransactionContext,
    TripItem,
)
from app.jobs.queue import PermanentJobError, RetryableJobError
from app.journey import place_resolver
from app.marketplace import discovery
from app.mobility.core import (
    MODE_SPECS,
    MobilityMode,
    request_from_plan_details,
    search_constraints,
)
from app.mobility.sandbox import (
    REGION,
    ensure_transfer_provider,
)
from app.transactions.context import get_or_create_for_trip
from app.transactions.providers.registry import ProviderRegistry
from app.transactions.service import TransactionService, TxnDeps
from app.whatsapp.mock import MockWhatsAppTransport


def plan_item(
    session: Session,
    trip_id: str,
    plan_key: str,
) -> TripItem | None:
    return session.scalar(
        select(TripItem).where(
            TripItem.trip_id == trip_id,
            TripItem.identity == plan_key,
            TripItem.kind == "plan",
        )
    )



def _region_for_request(request) -> str | None:
    """Fail closed when the journey has no verified provider region."""
    origin = place_resolver.resolve_place(request.origin)
    destination = place_resolver.resolve_place(request.destination)

    if origin is None or destination is None:
        return None

    if (
        origin["country"] == "Montenegro"
        and destination["country"] == "Montenegro"
    ):
        return REGION

    return None

def _discover(
    session: Session,
    request,
    mode: MobilityMode,
    *,
    now: datetime,
):
    spec = MODE_SPECS[mode]
    details = search_constraints(request, mode)

    start = (
        request.leave_by_local
        if mode in (
            MobilityMode.PRIVATE_TRANSFER,
            MobilityMode.RENTAL_CAR,
        )
        else None
    )

    return discovery.discover(
        session,
        service_type=spec.service_type,
        details=details,
        region=_region_for_request(request),
        property_id=None,
        now=now,
        tz=request.timezone,
        start=start,
    )


def compare(
    session: Session,
    plan: TripItem,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Compare modes using actual marketplace evidence.

    The synthetic transfer provider is development infrastructure only.
    No Quote or ExternalTransaction is created here.
    """

    request = request_from_plan_details(
        plan.details,
        require_party_size=False,
    )

    if _region_for_request(request) == REGION:
        ensure_transfer_provider(session)
    modes = []

    for mode in MobilityMode:
        result = _discover(
            session,
            request,
            mode,
            now=now,
        )

        providers = []

        for candidate in [
            *result.candidates,
            *result.pending,
        ]:
            if candidate.provider.id not in {
                p["id"] for p in providers
            }:
                providers.append({
                    "id": candidate.provider.id,
                    "slug": candidate.provider.slug,
                    "name": candidate.provider.name,
                    "sandbox": bool(
                        (candidate.provider.profile or {})
                        .get("synthetic")
                    ),
                })

        if result.candidates:
            status = "available"
            reason = "provider_candidate_found"
        elif result.pending:
            status = "needs_details"
            reason = "provider_requires_more_details"
        else:
            status = "unavailable"
            reason = result.reason or "no_provider_evidence"

        modes.append({
            "mode": mode.value,
            "service_type": MODE_SPECS[mode].service_type,
            "status": status,
            "reason": reason,
            "providers": providers,
            "quote_created": False,
            "booking_created": False,
        })

    return {
        "request": {
            "origin": request.origin,
            "destination": request.destination,
            "party_size": request.party_size,
            "arrive_by_local":
                request.arrive_by_local.isoformat(),
            "leave_by_local": (
                request.leave_by_local.isoformat()
                if request.leave_by_local
                else None
            ),
            "timezone": request.timezone,
        },
        "modes": modes,
    }



def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _transaction_service(
    session: Session,
    now: datetime,
) -> TransactionService:
    clock = FrozenClock(now)
    outbox = MockWhatsAppTransport()

    return TransactionService(
        session,
        TxnDeps(
            clock=clock,
            providers=ProviderRegistry(clock),
            executors=default_executors(),
            transport_for=lambda _: outbox,
        ),
    )


def _reusable_transfer_quote(
    session: Session,
    context_id: str,
    expected: dict[str, Any],
    *,
    now: datetime,
    provider_id: str | None = None,
) -> tuple[Quote, ExternalProvider] | None:
    """Find one exact, still-valid, unaccepted transfer offer.

    Expired OFFERED quotes are transitioned to EXPIRED while scanning.
    Quotes for a different request remain open until a replacement quote
    is successfully obtained, at which point request_quote supersedes them.
    """

    service = _transaction_service(
        session,
        now,
    )

    rows = session.execute(
        select(Quote, ExternalProvider)
        .join(
            ExternalProvider,
            ExternalProvider.id == Quote.provider_id,
        )
        .where(
            Quote.transaction_context_id == context_id,
            Quote.service_type == "intercity_transfer",
            Quote.status == QuoteStatus.OFFERED,
        )
        .order_by(Quote.created_at.desc())
    ).all()

    for quote, provider in rows:
        if service.is_expired(quote):
            service.expire(quote)
            continue

        if (
            provider_id is not None
            and quote.provider_id != provider_id
        ):
            continue

        transaction = session.scalar(
            select(ExternalTransaction).where(
                ExternalTransaction.quote_id == quote.id,
            )
        )

        if transaction is not None:
            continue

        if any(
            quote.request.get(key) != value
            for key, value in expected.items()
        ):
            continue

        return quote, provider

    return None


def active_transfer_quote(
    session: Session,
    trip_id: str,
    plan: TripItem,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Return the current valid Quote for this exact plan state.

    Reading may expire an OFFERED quote whose valid_until has passed.
    It never creates a Quote, transaction or booking.
    """

    context = session.scalar(
        select(TransactionContext).where(
            TransactionContext.trip_id == trip_id,
        )
    )

    if context is None:
        return None

    try:
        request = request_from_plan_details(
            plan.details,
        )
    except ValueError as exc:
        if str(exc) == "party_size_missing":
            return None
        raise

    if _region_for_request(request) != REGION:
        return None

    expected = search_constraints(
        request,
        MobilityMode.PRIVATE_TRANSFER,
    )

    match = _reusable_transfer_quote(
        session,
        context.id,
        expected,
        now=now,
    )

    if match is None:
        return None

    quote, provider = match

    return quote_payload(
        quote,
        provider.name,
    )


def create_transfer_quote(
    session: Session,
    trip_id: str,
    plan: TripItem,
    *,
    now: datetime,
) -> tuple[Quote, str]:
    """Create a provider Quote. Never create a booking."""

    request = request_from_plan_details(plan.details)

    if _region_for_request(request) != REGION:
        raise ValueError("no_transfer_provider_for_region")

    provider = ensure_transfer_provider(session)

    details = search_constraints(
        request,
        MobilityMode.PRIVATE_TRANSFER,
    )

    # Replacement is allowed only for an accepted booking
    # belonging to this exact Atlas trip and plan.
    previous = current_transfer_transaction(
        session,
        trip_id,
        plan,
    )

    # Never create an independent booking while the current
    # transfer is active, ambiguous, or already in progress.
    if previous is not None and previous.action.status not in {
        ActionStatus.ACCEPTED,
        ActionStatus.CANCELLED,
        ActionStatus.REJECTED,
        ActionStatus.FAILED,
    }:
        raise ValueError("existing_transfer_not_replaceable")

    # An earlier replacement may have succeeded while cancellation
    # of the old booking failed or is still pending.
    # Do not create another Quote until that obligation is resolved.
    if (
        previous is not None
        and previous.quote.replaces_transaction_id
    ):
        old_transaction = session.get(
            ExternalTransaction,
            previous.quote.replaces_transaction_id,
        )

        if (
            old_transaction is None
            or old_transaction.quote.transaction_context_id
            != previous.quote.transaction_context_id
            or old_transaction.action.status
            != ActionStatus.CANCELLED
        ):
            raise ValueError("unresolved_previous_change")

    replaces = None

    if (
        previous is not None
        and previous.action.status == ActionStatus.ACCEPTED
    ):
        # Never make a second booking for an unchanged request.
        if all(
            previous.request.get(key) == value
            for key, value in details.items()
        ):
            raise ValueError("transfer_already_confirmed")

        replaces = previous

    existing_context = session.scalar(
        select(TransactionContext).where(
            TransactionContext.trip_id == trip_id,
        )
    )

    if existing_context is not None:
        reusable = _reusable_transfer_quote(
            session,
            existing_context.id,
            details,
            now=now,
            provider_id=provider.id,
        )

        if reusable is not None:
            quote, existing_provider = reusable
            return quote, existing_provider.name

    result = _discover(
        session,
        request,
        MobilityMode.PRIVATE_TRANSFER,
        now=now,
    )

    candidate = next(
        (
            c
            for c in result.candidates
            if c.provider.id == provider.id
        ),
        None,
    )

    if candidate is None:
        raise ValueError(
            result.reason or "no_transfer_provider"
        )

    context = get_or_create_for_trip(
        session,
        trip_id,
        timezone=request.timezone,
        locale="en",
    )

    service = _transaction_service(
        session,
        now,
    )

    quote = service.request_quote(
        context=context,
        provider=candidate.provider,
        service_type="intercity_transfer",
        details=details,
        locale="en",
        candidate=candidate,
        replaces=replaces,
    )

    # Defensive invariant: requesting a quote can never make a booking.
    transaction = session.scalar(
        select(ExternalTransaction).where(
            ExternalTransaction.quote_id == quote.id,
        )
    )

    if transaction is not None:
        raise RuntimeError(
            "quote unexpectedly created a transaction"
        )

    return quote, candidate.provider.name


def quote_payload(
    quote: Quote,
    provider_name: str,
) -> dict[str, Any]:
    return {
        "id": quote.id,
        "code": quote.code,
        "status": quote.status.value,
        "provider": provider_name,
        "sandbox": True,
        "service_type": quote.service_type,
        "amount": f"{quote.amount:.2f}",
        "currency": quote.currency,
        "description": quote.description,
        "conditions": quote.conditions,
        "valid_until": quote.valid_until.isoformat(),
        "request": {
            k: v
            for k, v in quote.request.items()
            if not k.startswith("_")
        },
        "booking_created": False,
        "transaction_created": False,
    }



def confirm_transfer_quote(
    session: Session,
    trip_id: str,
    plan: TripItem,
    quote_id: str,
    *,
    consent: bool,
    now: datetime,
) -> ExternalTransaction:
    """Explicitly accept one sandbox transfer Quote.

    This is deliberately sandbox-only while Atlas sessions are ephemeral.
    Real providers will use the durable worker path once Atlas transaction
    persistence is moved out of the demo SessionStore.
    """

    if consent is not True:
        raise ValueError("explicit_consent_required")

    quote = session.get(Quote, quote_id)

    if quote is None:
        raise ValueError("quote_not_found")

    if quote.service_type != "intercity_transfer":
        raise ValueError("wrong_quote_service")

    if not quote.transaction_context_id:
        raise ValueError("quote_has_no_atlas_context")

    context = session.get(
        TransactionContext,
        quote.transaction_context_id,
    )

    if context is None or context.trip_id != trip_id:
        raise ValueError("quote_belongs_to_another_trip")

    provider = session.get(
        ExternalProvider,
        quote.provider_id,
    )

    if (
        provider is None
        or provider.slug != "atlas-sandbox-transfer"
    ):
        raise ValueError(
            "only_sandbox_confirmation_is_enabled"
        )

    # The user may have changed origin/time/party size after getting a quote.
    # Never accept a stale quote for an old version of the plan.
    request = request_from_plan_details(plan.details)

    expected = search_constraints(
        request,
        MobilityMode.PRIVATE_TRANSFER,
    )

    for key, value in expected.items():
        if quote.request.get(key) != value:
            raise ValueError(
                "quote_no_longer_matches_plan"
            )

    clock = FrozenClock(now)
    outbox = MockWhatsAppTransport()

    service = TransactionService(
        session,
        TxnDeps(
            clock=clock,
            providers=ProviderRegistry(clock),
            executors=default_executors(),
            transport_for=lambda _: outbox,
        ),
    )

    # Double-click / browser retry: one Quote can only produce one txn.
    existing = session.scalar(
        select(ExternalTransaction).where(
            ExternalTransaction.quote_id == quote.id
        )
    )

    if existing is not None:
        txn = existing
    else:
        txn = service.accept(
            quote,
            message_id=f"atlas-web:{quote.id}",
            text=(
                f"Confirm {quote.amount:.2f} "
                f"{quote.currency}"
            ),
        )

    # Atlas demo sessions have no background worker. For the deterministic
    # sandbox provider we execute the SAME provider-submit domain operation
    # inline, then mark its queued outbox job completed.
    submitted_now = txn.action.status == ActionStatus.PROPOSED

    if submitted_now:
        service.run_submit(txn.id)

        job = session.scalar(
            select(Job).where(
                Job.idempotency_key
                == f"submit:{txn.id}"
            )
        )

        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.attempts = max(job.attempts, 1)
            job.completed_at = now
            job.last_error = None
            job.locked_by = None
            job.locked_at = None

    session.flush()

    # The domain engine queues cancellation only after the
    # replacement has been accepted by the new provider.
    # Atlas Web runs this operation inline for sandbox only.
    if (
        submitted_now
        and txn.action.status == ActionStatus.ACCEPTED
        and quote.replaces_transaction_id
    ):
        previous = session.get(
            ExternalTransaction,
            quote.replaces_transaction_id,
        )

        if (
            previous is not None
            and previous.provider.slug == "atlas-sandbox-transfer"
            and previous.action.status == ActionStatus.ACCEPTED
        ):
            try:
                cancel_transfer(
                    session,
                    trip_id,
                    plan,
                    transaction_id=previous.id,
                    now=now,
                )
            except RetryableJobError:
                # The new booking has already been accepted.
                # Preserve both transactions and the pending
                # cancellation job for later reconciliation.
                pass
            except PermanentJobError:
                # No automatic retries for a permanent failure.
                # Both booking records remain available for review.
                job = session.scalar(
                    select(Job).where(
                        Job.idempotency_key
                        == f"cancel:{previous.id}"
                    )
                )

                if job is not None:
                    job.status = JobStatus.DEAD
                    job.attempts = max(job.attempts, 1)
                    job.completed_at = now
                    job.last_error = (
                        "sandbox cancellation permanently failed"
                    )
                    job.locked_by = None
                    job.locked_at = None

    if txn.action.status in {
        ActionStatus.ACCEPTED,
        ActionStatus.IN_PROGRESS,
        ActionStatus.COMPLETED,
    }:
        plan.details = {
            **(plan.details or {}),
            "transport_transaction": {
                "transaction_id": txn.id,
                "action_id": txn.action_id,
                "quote_id": quote.id,
                "status": txn.action.status.value,
                "provider": txn.provider.name,
                "provider_slug": txn.provider.slug,
                "provider_reference":
                    txn.provider_reference,
                "pickup":
                    quote.request.get("pickup"),
                "destination":
                    quote.request.get("destination"),
                "pickup_time":
                    quote.request.get("pickup_time"),
                "party_size":
                    quote.request.get("party_size"),
                "amount": f"{quote.amount:.2f}",
                "currency": quote.currency,
                "sandbox": True,
            },
        }

        session.flush()

    return txn



def current_transfer_transaction(
    session: Session,
    trip_id: str,
    plan: TripItem,
) -> ExternalTransaction | None:
    """Return this plan's transaction only if it belongs to this Atlas trip."""
    state = (plan.details or {}).get("transport_transaction")

    if not isinstance(state, dict):
        return None

    transaction_id = state.get("transaction_id")
    if not transaction_id:
        return None

    txn = session.get(ExternalTransaction, transaction_id)
    if txn is None:
        return None

    quote = txn.quote

    if (
        quote.service_type != "intercity_transfer"
        or not quote.transaction_context_id
    ):
        return None

    context = session.get(
        TransactionContext,
        quote.transaction_context_id,
    )

    if context is None or context.trip_id != trip_id:
        return None

    return txn


def cancellation_payload(
    session: Session,
    trip_id: str,
    plan: TripItem,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    txn = current_transfer_transaction(
        session,
        trip_id,
        plan,
    )

    if txn is None:
        return None

    service = _transaction_service(session, now)
    policy = service.cancellation_policy(txn)

    return {
        "transaction_id": txn.id,
        "transaction_status": txn.action.status.value,
        "cancellable":
            txn.action.status in {
                ActionStatus.PROPOSED,
                ActionStatus.SUBMITTED,
                ActionStatus.PENDING_CONDITION,
                ActionStatus.ACCEPTED,
            },
        "policy": {
            "status": policy.status,
            "deadline":
                policy.deadline.isoformat()
                if policy.deadline
                else None,
            "fee": policy.fee,
        },
        "provider": txn.provider.name,
        "provider_reference": txn.provider_reference,
        "sandbox":
            txn.provider.slug
            == "atlas-sandbox-transfer",
    }


def cancel_transfer(
    session: Session,
    trip_id: str,
    plan: TripItem,
    *,
    transaction_id: str,
    now: datetime,
) -> ExternalTransaction:
    txn = current_transfer_transaction(
        session,
        trip_id,
        plan,
    )

    if txn is None:
        raise ValueError("confirmed_transfer_not_found")

    if not transaction_id or txn.id != transaction_id:
        raise ValueError("cancellation_transaction_mismatch")

    # Idempotent replay for the same completed cancellation.
    # No second provider call or cancellation job.
    if txn.action.status == ActionStatus.CANCELLED:
        return txn

    service = _transaction_service(session, now)

    if not service.request_cancel(txn):
        raise ValueError(
            f"transfer_not_cancellable:{txn.action.status.value}"
        )

    # Atlas Web currently executes the deterministic sandbox provider inline.
    # Real providers keep the durable worker path.
    if (
        txn.provider.slug == "atlas-sandbox-transfer"
        and txn.action.status != ActionStatus.CANCELLED
    ):
        service.run_cancel(txn.id)

        job = session.scalar(
            select(Job).where(
                Job.idempotency_key
                == f"cancel:{txn.id}"
            )
        )

        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.attempts = max(job.attempts, 1)
            job.completed_at = now
            job.last_error = None
            job.locked_by = None
            job.locked_at = None

    # Keep the historical plan snapshot in sync for compatibility.
    # Public truth still comes from ExternalTransaction.action.status.
    state = dict(
        (plan.details or {}).get(
            "transport_transaction"
        ) or {}
    )

    plan.details = {
        **(plan.details or {}),
        "transport_transaction": {
            **state,
            "status": txn.action.status.value,
        },
    }

    session.flush()

    return txn


def transaction_payload(
    txn: ExternalTransaction,
) -> dict[str, Any]:
    quote = txn.quote

    return {
        "id": txn.id,
        "action_id": txn.action_id,
        "quote_id": txn.quote_id,
        "status": txn.action.status.value,
        "provider": txn.provider.name,
        "provider_reference":
            txn.provider_reference,
        "service_type": quote.service_type,
        "amount": f"{quote.amount:.2f}",
        "currency": quote.currency,
        "sandbox": (
            txn.provider.slug
            == "atlas-sandbox-transfer"
        ),
        "transaction_created": True,
        "provider_accepted": (
            txn.action.status
            in {
                ActionStatus.ACCEPTED,
                ActionStatus.IN_PROGRESS,
                ActionStatus.COMPLETED,
            }
        ),
        "real_world": False,
    }
