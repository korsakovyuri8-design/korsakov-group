"""Explicit traveller answers to Atlas planning clarifications.

Answers become evidence-backed facts on a stated plan.
They never create or confirm bookings.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TripItem, TripObservation, TripSourceRecord


PLACE_ROLES = {"venue", "stay", "both"}


def _item(
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


def _set(
    session: Session,
    trip_id: str,
    plan_key: str,
    field: str,
    value: Any,
    evidence: str,
    *,
    received_at: datetime,
) -> tuple[TripItem, bool] | None:
    item = _item(session, trip_id, plan_key)

    if item is None:
        return None

    if (item.details or {}).get(field) == value:
        return item, False

    raw = json.dumps(
        {
            "type": "plan_answer",
            "plan_key": plan_key,
            "field": field,
            "value": value,
            "evidence": evidence,
            "answered_at": received_at.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    record = TripSourceRecord(
        trip_id=trip_id,
        kind="guest_statement",
        source_id=f"ui:plan_answer:{field}:{received_at.isoformat()}",
        received_at=received_at,
        issued_at=received_at,
        raw=raw,
        raw_sha256=digest,
        normalized_sha256=digest,
    )

    session.add(record)
    session.flush()

    revisions = [dict(r) for r in (item.revisions or [])]
    seq = max(
        (int(r.get("revision") or 0) for r in revisions),
        default=0,
    ) + 1

    session.add(
        TripObservation(
            trip_id=trip_id,
            record_id=record.id,
            item_id=item.id,
            field=field,
            value=value,
            evidence=evidence,
            revision=seq,
            extractor="user:plan_answer",
        )
    )

    revisions.append({
        "revision": seq,
        "record_ids": [record.id],
        "issued_at": received_at.isoformat(),
        "details": {field: value},
        "times": {},
        "changed_fields": [field],
    })

    next_details = {
        **(item.details or {}),
        field: value,
    }

    if field in {"origin_text", "arrival_time", "date_text"}:
        next_details.pop("route_plan", None)

    item.details = next_details
    item.revisions = revisions
    item.current_revision = seq

    session.flush()

    return item, True


def set_place_role(
    session: Session,
    trip_id: str,
    plan_key: str,
    role: str,
    *,
    received_at: datetime,
):
    if role not in PLACE_ROLES:
        raise ValueError("invalid place role")

    labels = {
        "venue": "Event venue",
        "stay": "My stay",
        "both": "Event venue and my stay",
    }

    return _set(
        session,
        trip_id,
        plan_key,
        "place_role",
        role,
        labels[role],
        received_at=received_at,
    )


def set_overnight_stay(
    session: Session,
    trip_id: str,
    plan_key: str,
    value: bool,
    *,
    received_at: datetime,
):
    return _set(
        session,
        trip_id,
        plan_key,
        "overnight_stay",
        value,
        "Yes, I need an overnight stay"
        if value
        else "No overnight stay",
        received_at=received_at,
    )


def set_origin_text(
    session: Session,
    trip_id: str,
    plan_key: str,
    origin: str,
    *,
    received_at: datetime,
):
    origin = origin.strip()

    if not origin or len(origin) > 200:
        raise ValueError("invalid origin")

    return _set(
        session,
        trip_id,
        plan_key,
        "origin_text",
        origin,
        origin,
        received_at=received_at,
    )


def set_arrival_time(
    session: Session,
    trip_id: str,
    plan_key: str,
    value: str,
    *,
    received_at: datetime,
):
    import re

    value = value.strip()

    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("invalid arrival time")

    return _set(
        session,
        trip_id,
        plan_key,
        "arrival_time",
        value,
        value,
        received_at=received_at,
    )


def set_party_size(
    session: Session,
    trip_id: str,
    plan_key: str,
    value: int,
    *,
    received_at: datetime,
):
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 20:
        raise ValueError("invalid party size")

    return _set(
        session,
        trip_id,
        plan_key,
        "party_size",
        value,
        f"{value} traveller{'s' if value != 1 else ''}",
        received_at=received_at,
    )



def set_date_text(
    session: Session,
    trip_id: str,
    plan_key: str,
    value: str,
    *,
    received_at: datetime,
):
    """Save an explicit date clarification on the same stated plan.

    Relative UI choices are converted deterministically to calendar text.
    They remain guest answers; they never become provider-confirmed facts.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from app.journey import date_resolver, place_resolver

    item = _item(session, trip_id, plan_key)
    if item is None:
        return None

    raw = value.strip()
    if not raw or len(raw) > 80:
        raise ValueError("invalid plan date")

    place = place_resolver.resolve_place(
        (item.details or {}).get("place")
    )
    timezone_name = (
        (place or {}).get("timezone")
        or "UTC"
    )

    today = received_at.astimezone(
        ZoneInfo(timezone_name)
    ).date()

    months = (
        "",
        "January", "February", "March", "April",
        "May", "June", "July", "August",
        "September", "October", "November", "December",
    )

    token = raw.casefold()

    if token == "tomorrow":
        day = today + timedelta(days=1)
        normalized = f"{day.day} {months[day.month]}"
        evidence = "Tomorrow"

    elif token == "this_weekend":
        # Saturday/Sunday of the current upcoming weekend.
        days_to_saturday = (5 - today.weekday()) % 7
        start = today + timedelta(days=days_to_saturday)
        end = start + timedelta(days=1)

        if start.month == end.month:
            normalized = (
                f"{start.day}–{end.day} "
                f"{months[start.month]}"
            )
        else:
            normalized = (
                f"{start.day} {months[start.month]}–"
                f"{end.day} {months[end.month]}"
            )

        evidence = "This weekend"

    else:
        if date_resolver.resolve_date_range(
            raw,
            now_utc=received_at,
            timezone=timezone_name,
        ) is None:
            raise ValueError("invalid plan date")

        normalized = raw
        evidence = raw

    return _set(
        session,
        trip_id,
        plan_key,
        "date_text",
        normalized,
        evidence,
        received_at=received_at,
    )
