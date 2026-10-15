"""One spec per trip-item kind (D-078): what its details may hold, which
observed fields are details or times, how a document finds its item, and
the anchor fixed at first sight. ingest.py stays one generic pipeline:
parse -> observations -> resolve the item -> revision -> status events ->
provenance -> issues. A new kind is a new spec, never a new branch there."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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

KINDS: dict[str, KindSpec] = {s.kind: s for s in (FLIGHT, ACCOMMODATION, RESTAURANT, STATEMENT)}
