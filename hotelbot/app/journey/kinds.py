"""One spec per trip-item kind (D-078): what its details may hold, which
observed fields are details or times, how a document finds its item, and
the anchor fixed at first sight. ingest.py stays one generic pipeline:
parse -> observations -> resolve the item -> revision -> status events ->
provenance -> issues. A new kind is a new spec, never a new branch there."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TripItem

# resolver(session, trip_id, parsed, values) -> (item | None, identity, outcome)
#   outcome: "existing" | "new" | "ambiguous" (never guess: an ambiguous document merges nothing)
Resolver = Callable[[Session, str, Any, dict[str, Any]], tuple[TripItem | None, str, str]]


@dataclass(frozen=True)
class KindSpec:
    kind: str
    details: dict[str, Any]                       # key -> allowed type(s)
    detail_fields: tuple[str, ...]                # observed fields copied into details
    time_fields: tuple[str, ...]                  # observed fields that are time values
    resolve: Resolver
    anchors: Callable[[dict[str, Any]], dict[str, Any]] = lambda values: {}   # details fixed at creation
    anchor_evidence: dict[str, str] = field(default_factory=dict)            # anchor key -> observed field
    derived: tuple[str, ...] = ()                 # details computed by code (e.g. comparisons), not claims


def _local_date(value: dict[str, Any] | None) -> str | None:
    return value["local"][:10] if value and value.get("local") else None


# ------------------------------------------------------------------ flight
def resolve_flight_segment(session: Session, trip_id: str, parsed, values: dict[str, Any]):  # noqa: ANN001, ANN201
    """Segment anchor identity (slice 1.1): provider + ref + carrier/number +
    route + ORIGINAL local departure date, fixed at first sight. A document
    finds its segment by 'Originally scheduled', else by a departure date
    equal to the anchor or to any revision's, else (change / cancellation)
    by the single candidate; more candidates -> ambiguous."""
    prefix = "|".join([parsed.kind, parsed.provider or "", parsed.booking_reference or "",
                       values.get("flight_number", ""), f"{values.get('origin', '')}-{values.get('destination', '')}"])
    candidates = list(session.scalars(select(TripItem).where(TripItem.trip_id == trip_id,
                                                             TripItem.identity.like(f"{prefix}|%"))))
    original = values.get("original_departure")
    anchor = _local_date(original) or _local_date(values.get("departs"))
    identity = f"{prefix}|{anchor}"
    if original:                                   # the document names the segment it changes
        hit = [c for c in candidates if c.details.get("service_date") == _local_date(original)]
        if len(hit) == 1:
            return hit[0], hit[0].identity, "existing"
        return None, identity, "ambiguous" if candidates else "new"

    def dates(c: TripItem) -> set[str]:
        out = {c.details.get("service_date")}
        for r in c.revisions:
            if (r["times"].get("departs") or {}).get("local"):
                out.add(r["times"]["departs"]["local"][:10])
        return out

    doc_date = _local_date(values.get("departs"))
    exact = [c for c in candidates if doc_date in dates(c)]
    if len(exact) == 1:
        return exact[0], exact[0].identity, "existing"
    if len(exact) > 1:
        return None, identity, "ambiguous"
    if parsed.doc_type in ("change", "cancellation") and candidates:
        if len(candidates) == 1:
            return candidates[0], candidates[0].identity, "existing"
        return None, identity, "ambiguous"
    return None, identity, "new"


FLIGHT = KindSpec(
    kind="flight",
    details={"carrier": str, "flight_number": str, "origin": str, "destination": str, "cabin": (str, type(None)),
             "passengers": list, "service_date": str},
    detail_fields=("carrier", "flight_number", "origin", "destination", "cabin", "passengers"),
    time_fields=("departs", "arrives"),
    resolve=resolve_flight_segment,
    anchors=lambda values: {"service_date": _local_date(values.get("original_departure"))
                            or _local_date(values.get("departs"))},
    anchor_evidence={"service_date": "departs"},
)

# ------------------------------------------------------------------ by booking reference
def resolve_by_reference(session: Session, trip_id: str, parsed, values: dict[str, Any]):  # noqa: ANN001, ANN201
    """One booking = provider + booking reference (a hotel stay, a table)."""
    identity = "|".join([parsed.kind, parsed.provider or "", parsed.booking_reference or ""])
    item = session.scalar(select(TripItem).where(TripItem.trip_id == trip_id, TripItem.identity == identity))
    return item, identity, "existing" if item else "new"


ACCOMMODATION = KindSpec(
    kind="accommodation",
    details={"guest_name": str, "address": str, "room": str, "guests": int, "check_in_from": str,
             "check_out_until": str},
    detail_fields=("guest_name", "address", "room", "guests", "check_in_from", "check_out_until"),
    time_fields=("check_in", "check_out"),
    resolve=resolve_by_reference,
)

RESTAURANT = KindSpec(
    kind="restaurant",
    details={"venue": str, "address": str, "party": int},
    detail_fields=("venue", "address", "party"),
    time_fields=("starts",),
    resolve=resolve_by_reference,
)


TRANSFER = KindSpec(
    kind="transfer",
    details={"pickup": str, "pickup_place_kind": str, "dropoff": str, "dropoff_place_kind": str, "passengers": int},
    detail_fields=("pickup", "pickup_place_kind", "dropoff", "dropoff_place_kind", "passengers"),
    time_fields=("pickup_at", "dropoff_at"),
    resolve=resolve_by_reference,
)


# ------------------------------------------------------------------ guest statement
def resolve_statement(session: Session, trip_id: str, parsed, values: dict[str, Any]):  # noqa: ANN001, ANN201
    """A guest statement is its own item - never merged into a booking it
    resembles; the same claim restated (same subject, place, day) is the
    same statement, a different time a revision of it."""
    identity = "|".join(["statement", values.get("subject", ""), (values.get("city") or "").lower(),
                         _local_date(values.get("at")) or ""])
    item = session.scalar(select(TripItem).where(TripItem.trip_id == trip_id, TripItem.identity == identity))
    return item, identity, "existing" if item else "new"


STATEMENT = KindSpec(
    kind="statement",
    details={"subject": str, "city": str, "related": list},
    detail_fields=("subject", "city"),
    time_fields=("at",),
    resolve=resolve_statement,
    derived=("related",),
)

# ------------------------------------------------------------------ stated plan
def _plan_norm(value: str | None) -> str:
    """Comparison form only; the user's original wording stays untouched."""
    return " ".join(
        (value or "")
        .replace("–", "-")
        .replace("—", "-")
        .lower()
        .split()
    )


def resolve_plan(session: Session, trip_id: str, parsed, values: dict[str, Any]):  # noqa: ANN001, ANN201
    """Resolve a stated plan conservatively.

    A later statement may REFINE one existing stated plan when:
    - the destination is the same;
    - explicit dates do not conflict;
    - explicit events do not conflict.

    A vague plan followed by exact dates is the same plan when there is only
    one compatible candidate. Two different explicit date ranges are separate
    trips. Multiple compatible candidates are ambiguous: Atlas never guesses.

    Stated plans are never merged into provider-backed bookings.
    """
    key = "|".join([
        values.get("date_text", ""),
        values.get("place", ""),
        values.get("text", ""),
    ])
    identity = "plan|" + hashlib.sha256(
        key.lower().encode("utf-8")
    ).hexdigest()[:20]

    # Exact re-delivery / same semantic identity first.
    exact = session.scalar(
        select(TripItem).where(
            TripItem.trip_id == trip_id,
            TripItem.identity == identity,
            TripItem.kind == "plan",
        )
    )
    if exact is not None:
        return exact, exact.identity, "existing"

    place = _plan_norm(values.get("place"))
    incoming_date = _plan_norm(values.get("date_text"))
    incoming_event = _plan_norm(values.get("event"))

    if not place:
        return None, identity, "new"

    candidates = list(
        session.scalars(
            select(TripItem).where(
                TripItem.trip_id == trip_id,
                TripItem.kind == "plan",
            )
        )
    )

    compatible: list[TripItem] = []

    for candidate in candidates:
        details = candidate.details or {}

        if _plan_norm(details.get("place")) != place:
            continue

        existing_date = _plan_norm(details.get("date_text"))
        existing_event = _plan_norm(details.get("event"))

        # Two different explicit dates mean two different trips.
        if (
            incoming_date
            and existing_date
            and incoming_date != existing_date
        ):
            continue

        # Two explicitly different events at the same place are not one plan.
        if (
            incoming_event
            and existing_event
            and incoming_event != existing_event
        ):
            continue

        # Do not let a new vague statement silently attach itself to an
        # already date-specific trip unless the event also identifies it.
        if (
            not incoming_date
            and existing_date
            and not (
                incoming_event
                and existing_event
                and incoming_event == existing_event
            )
        ):
            continue

        compatible.append(candidate)

    if len(compatible) == 1:
        item = compatible[0]
        return item, item.identity, "existing"

    if len(compatible) > 1:
        return None, identity, "ambiguous"

    return None, identity, "new"


PLAN = KindSpec(
    kind="plan",
    details={
        "text": str,
        "event": str,
        "place": str,
        "date_text": str,
        "place_role": str,
        "overnight_stay": bool,
        "origin_text": str,
        "arrival_time": str,
        "party_size": int, "timing_text": str, "duration_text": str, "note": str},
    detail_fields=("text", "event", "place", "date_text", "origin_text", "timing_text", "duration_text", "note"),
    time_fields=(),
    resolve=resolve_plan,
)

KINDS: dict[str, KindSpec] = {s.kind: s for s in (FLIGHT, ACCOMMODATION, RESTAURANT, TRANSFER, STATEMENT, PLAN)}
