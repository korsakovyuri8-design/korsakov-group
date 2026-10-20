"""Deterministic planning gaps for stated Atlas plans.

Planning gaps are not booking issues. They describe which facts Atlas still
needs or which explicitly-required parts of a plan lack confirmed evidence.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from app.journey import place_resolver
from zoneinfo import ZoneInfoNotFoundError


CONFIRMED = {"confirmed", "booked"}


def _norm(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _destination_terms(resolved_place: dict[str, Any] | None) -> set[str]:
    if not resolved_place:
        return set()

    return {
        _norm(v)
        for v in (
            resolved_place.get("canonical_name"),
            resolved_place.get("locality"),
            resolved_place.get("municipality"),
            resolved_place.get("address"),
        )
        if _norm(v)
    }


def _matches(value: str | None, terms: set[str]) -> bool:
    text = _norm(value)
    if not text:
        return False

    return any(
        text == term or term in text or text in term
        for term in terms
    )


def _confirmed_accommodation(
    items: Iterable[Any],
    *,
    start: date,
    end: date,
    destination_terms: set[str],
) -> bool:
    for item in items:
        if getattr(item, "kind", None) != "accommodation":
            continue
        if getattr(item, "status", None) not in CONFIRMED:
            continue

        d = getattr(item, "details", {}) or {}
        t = getattr(item, "times", {}) or {}

        check_in = _day((t.get("check_in") or {}).get("local"))
        check_out = _day((t.get("check_out") or {}).get("local"))

        if check_in is None or check_out is None:
            continue

        if not (check_in <= start and check_out >= end):
            continue

        location = " ".join(
            x for x in (
                d.get("address"),
                getattr(item, "provider", None),
            )
            if x
        )

        if not destination_terms or _matches(location, destination_terms):
            return True

    return False


def _latest_confirmed_origin(
    items: Iterable[Any],
    *,
    start: date,
) -> tuple[str | None, str | None]:
    candidates: list[tuple[str, str]] = []

    for item in items:
        if getattr(item, "status", None) not in CONFIRMED:
            continue

        d = getattr(item, "details", {}) or {}
        t = getattr(item, "times", {}) or {}

        if getattr(item, "kind", None) == "transfer":
            rec = t.get("dropoff_at") or {}
            day = _day(rec.get("local"))
            place = d.get("dropoff")
        elif getattr(item, "kind", None) == "flight":
            rec = t.get("arrives") or {}
            day = _day(rec.get("local"))
            place = d.get("destination")
        else:
            continue

        if day is None or place is None or day > start:
            continue

        candidates.append((rec.get("local") or "", place))

    if not candidates:
        return None, None

    _, place = max(candidates)
    return place, "confirmed_previous_item"



def _confirmed_transaction_transport(
    detail: dict[str, Any],
    *,
    origin: str,
    destination_terms: set[str],
) -> bool:
    """Whether this plan already has provider-confirmed transport.

    Provider transaction evidence proves the booking and pickup.
    Atlas route_plan remains a planning estimate and is used only to check
    whether the confirmed pickup is early enough for the arrival deadline.
    """

    tx = detail.get("transport_transaction")

    if not isinstance(tx, dict):
        return False

    if tx.get("status") not in {
        "accepted",
        "in_progress",
        "completed",
    }:
        return False

    # A confirmed provider booking must match the current party.
    required_party = detail.get("party_size")
    booked_party = tx.get("party_size")

    if (
        type(required_party) is not int
        or type(booked_party) is not int
        or required_party != booked_party
    ):
        return False

    # Origin must match the traveller's stated/derived origin.
    if not _matches(
        tx.get("pickup"),
        {_norm(origin)},
    ):
        return False

    # Match against both resolver-derived destination aliases and the
    # traveller's original stated place.
    effective_destination_terms = set(destination_terms)

    stated_destination = detail.get("place")

    if stated_destination:
        effective_destination_terms.add(
            _norm(stated_destination)
        )

    if (
        not effective_destination_terms
        or not _matches(
            tx.get("destination"),
            effective_destination_terms,
        )
    ):
        return False

    route = detail.get("route_plan")

    if not isinstance(route, dict):
        return False

    pickup_raw = tx.get("pickup_time")
    leave_raw = route.get("leave_by_local")

    if not pickup_raw or not leave_raw:
        return False

    timezone_name = route.get("timezone") or "UTC"

    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(str(timezone_name))

        def wall(value: str) -> datetime:
            dt = datetime.fromisoformat(value)

            if dt.tzinfo is not None:
                dt = dt.astimezone(zone)

            # Compare local wall times in the journey timezone.
            return dt.replace(tzinfo=None)

        pickup = wall(str(pickup_raw))
        leave_by = wall(str(leave_raw))

    except (
        TypeError,
        ValueError,
        ZoneInfoNotFoundError,
    ):
        return False

    # A pickup at or before Atlas' leave-by time can satisfy the plan.
    return pickup <= leave_by


def _confirmed_arrival_transport(
    items: Iterable[Any],
    *,
    start: date,
    origin: str,
    destination_terms: set[str],
    arrival_by_local: str,
) -> bool:
    earliest = start - timedelta(days=1)
    deadline = datetime.fromisoformat(arrival_by_local)
    origin_terms = {_norm(origin)}

    for item in items:
        if getattr(item, "kind", None) != "transfer":
            continue
        if getattr(item, "status", None) not in CONFIRMED:
            continue

        d = getattr(item, "details", {}) or {}
        t = getattr(item, "times", {}) or {}

        dropoff_local = (t.get("dropoff_at") or {}).get("local")
        arrived = _day(dropoff_local)

        if arrived is None or not (earliest <= arrived <= start):
            continue

        try:
            dropoff_at = datetime.fromisoformat(dropoff_local)
        except (TypeError, ValueError):
            continue

        if dropoff_at > deadline:
            continue

        if not _matches(d.get("pickup"), origin_terms):
            continue

        if not _matches(d.get("dropoff"), destination_terms):
            continue

        return True

    return False


def needs_for_plan(
    detail: dict[str, Any],
    trip_items: Iterable[Any],
) -> dict[str, Any]:
    items = list(trip_items)

    place_role = detail.get("place_role")
    overnight = detail.get("overnight_stay")
    stated_origin = detail.get("origin_text")

    resolved_place = detail.get("resolved_place")
    resolved_date = detail.get("resolved_date")

    start = _day((resolved_date or {}).get("start_date"))
    end = _day((resolved_date or {}).get("end_date"))

    destination_terms = _destination_terms(resolved_place)

    needs: list[dict[str, str]] = []

    # A trip with a stated destination but no resolvable date cannot safely
    # advance into stay / arrival / transport planning. Ask for time first.
    if detail.get("place") and resolved_date is None:
        needs.append({
            "code": "date_unknown",
            "category": "clarification",
            "state": "needs_confirmation",
            "basis": "destination_known_without_resolved_date",
        })

        return {
            "status": "needs_planning",
            "needs": needs,
            "answers": {
                "date_text": detail.get("date_text"),
                "place_role": place_role,
                "overnight_stay": overnight,
                "origin_text": stated_origin,
                "arrival_time": detail.get("arrival_time"),
                "party_size": detail.get("party_size"),
            },
            "derived": {
                "arrival_origin": stated_origin,
                "arrival_by_local": None,
                "arrival_origin_basis": "guest" if stated_origin else None,
            },
        }

    if detail.get("event") and detail.get("place") and not place_role:
        needs.append({
            "code": "place_role_unknown",
            "category": "clarification",
            "state": "needs_confirmation",
            "basis": "event_and_place_stated_without_relationship",
        })

    has_confirmed_stay = False
    if start is not None and end is not None:
        has_confirmed_stay = _confirmed_accommodation(
            items,
            start=start,
            end=end,
            destination_terms=destination_terms,
        )

    # If the user explicitly says this place is their stay, that implies
    # overnight intent but does NOT imply a booking.
    stay_intended = (
        place_role in {"stay", "both"}
        or overnight is True
    )

    if start is not None and end is not None and not has_confirmed_stay:
        if stay_intended:
            needs.append({
                "code": "accommodation_unconfirmed",
                "category": "stay",
                "state": "unconfirmed",
                "basis": "overnight_stay_intended_without_confirmed_accommodation",
            })
        elif overnight is None and end > start:
            needs.append({
                "code": "overnight_stay_unknown",
                "category": "clarification",
                "state": "needs_confirmation",
                "basis": "multi_day_plan_without_stay_intent",
            })

    derived_origin = None
    derived_origin_basis = None

    if start is not None:
        derived_origin, derived_origin_basis = _latest_confirmed_origin(
            items,
            start=start,
        )

    origin = stated_origin or derived_origin
    resolved_origin = (
        place_resolver.resolve_place(origin)
        if isinstance(origin, str) and origin.strip()
        else None
    )
    arrival_time = detail.get("arrival_time")
    arrival_by_local = (
        f"{start.isoformat()}T{arrival_time}"
        if start is not None and arrival_time
        else None
    )

    if start is not None and resolved_place:
        if not origin:
            needs.append({
                "code": "arrival_origin_unknown",
                "category": "clarification",
                "state": "needs_confirmation",
                "basis": "no_stated_or_confirmed_origin_before_plan",
            })
        elif resolved_origin is None:
            needs.append({
                "code": "arrival_origin_unresolved",
                "category": "clarification",
                "state": "needs_confirmation",
                "basis": "origin_text_not_resolved_by_place_registry",
            })
        elif not arrival_time:
            needs.append({
                "code": "arrival_time_unknown",
                "category": "clarification",
                "state": "needs_confirmation",
                "basis": "origin_and_destination_known_without_arrival_deadline",
            })
        elif (
            not _confirmed_transaction_transport(
                detail,
                origin=origin,
                destination_terms=destination_terms,
            )
            and not _confirmed_arrival_transport(
                items,
                start=start,
                origin=origin,
                destination_terms=destination_terms,
                arrival_by_local=arrival_by_local,
            )
        ):
            needs.append({
                "code": "arrival_transport_unconfirmed",
                "category": "transport",
                "state": "unconfirmed",
                "basis": "route_known_without_confirmed_transport",
            })

    return {
        "status": "needs_planning" if needs else "covered",
        "needs": needs,
        "answers": {
            "place_role": place_role,
            "overnight_stay": overnight,
            "origin_text": stated_origin,
            "arrival_time": arrival_time,
            "party_size": detail.get("party_size"),
        },
        "derived": {
            "arrival_origin": origin,
            "arrival_by_local": arrival_by_local,
            "arrival_origin_basis": (
                "guest"
                if stated_origin
                else derived_origin_basis
            ),
        },
    }
