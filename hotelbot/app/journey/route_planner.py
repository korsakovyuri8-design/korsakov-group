"""Atlas road-route planning.

A route estimate is derived data, not a booking and not observed traveller
location. OSRM supplies distance/duration. Atlas policy supplies the planning
buffer. The two are always exposed separately.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TripItem, TripObservation, TripSourceRecord
from app.journey import date_resolver, place_resolver


OSRM_URL = "https://router.project-osrm.org"
USER_AGENT = "Korsakov-Atlas/0.1 (atlas.korsakovgroup.com)"

POLICY_VERSION = "2026-10-17.1"
ROAD_UNCERTAINTY_BUFFER_MINUTES = 30


class RoutePlanError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def fetch_route(
    origin: dict[str, Any],
    destination: dict[str, Any],
) -> dict[str, Any]:
    """Fetch one driving estimate. No user-controlled URL or hostname."""

    a = origin["coordinates"]
    b = destination["coordinates"]

    url = (
        f"{OSRM_URL}/route/v1/driving/"
        f"{a['lon']},{a['lat']};{b['lon']},{b['lat']}"
        "?overview=false&steps=false"
    )

    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RoutePlanError(
            "routing_unavailable",
            "Atlas could not calculate the road route right now.",
            502,
        ) from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutePlanError(
            "route_not_found",
            "Atlas could not find a driving route.",
            422,
        )

    route = data["routes"][0]

    return {
        "distance_m": int(round(route["distance"])),
        "duration_seconds": int(round(route["duration"])),
    }


def build_route_plan(
    *,
    origin: dict[str, Any],
    destination: dict[str, Any],
    arrival_by_local: str,
    estimate: dict[str, Any],
    generated_at: datetime,
) -> dict[str, Any]:
    timezone_name = destination["timezone"]
    zone = ZoneInfo(timezone_name)

    arrive_naive = datetime.fromisoformat(arrival_by_local)
    arrive = arrive_naive.replace(tzinfo=zone)

    route_minutes = int(round(estimate["duration_seconds"] / 60))
    buffer_minutes = ROAD_UNCERTAINTY_BUFFER_MINUTES

    leave = arrive - timedelta(
        minutes=route_minutes + buffer_minutes
    )

    return {
        "status": "estimated",
        "mode": "driving",
        "origin": {
            "entity_key": origin["entity_key"],
            "name": origin["canonical_name"],
            "locality": origin["locality"],
            "coordinates": origin["coordinates"],
        },
        "destination": {
            "entity_key": destination["entity_key"],
            "name": destination["canonical_name"],
            "locality": destination["locality"],
            "coordinates": destination["coordinates"],
        },
        "distance_km": round(estimate["distance_m"] / 1000, 1),
        "route_duration_minutes": route_minutes,
        "planning_buffer_minutes": buffer_minutes,
        "arrive_by_local": arrive.strftime("%Y-%m-%dT%H:%M"),
        "leave_by_local": leave.strftime("%Y-%m-%dT%H:%M"),
        "timezone": timezone_name,
        "traffic_live": False,
        "provider": "osrm_demo",
        "provider_guarantee": False,
        "policy_version": POLICY_VERSION,
        "basis": "external_route_estimate_plus_atlas_policy_buffer",
        "generated_at": generated_at.isoformat(),
    }


def plan_and_store(
    session: Session,
    trip_id: str,
    plan_key: str,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    item = session.scalar(
        select(TripItem).where(
            TripItem.trip_id == trip_id,
            TripItem.identity == plan_key,
            TripItem.kind == "plan",
        )
    )

    if item is None:
        raise RoutePlanError(
            "plan_not_found",
            "That stated plan is no longer available.",
            404,
        )

    details = item.details or {}

    origin_text = details.get("origin_text")
    destination_text = details.get("place")
    arrival_time = details.get("arrival_time")
    date_text = details.get("date_text")

    if not origin_text:
        raise RoutePlanError(
            "origin_missing",
            "Atlas needs an origin before planning transport.",
        )

    if not arrival_time:
        raise RoutePlanError(
            "arrival_time_missing",
            "Atlas needs an arrival time before planning transport.",
        )

    origin = place_resolver.resolve_place(origin_text)
    if origin is None:
        raise RoutePlanError(
            "origin_unresolved",
            "Atlas cannot resolve the origin yet.",
        )

    destination = place_resolver.resolve_place(destination_text)
    if destination is None:
        raise RoutePlanError(
            "destination_unresolved",
            "Atlas cannot resolve the destination yet.",
        )

    resolved_date = date_resolver.resolve_date_range(
        date_text,
        now_utc=generated_at,
        timezone=destination["timezone"],
    )

    if resolved_date is None:
        raise RoutePlanError(
            "date_unresolved",
            "Atlas cannot resolve the plan date yet.",
        )

    arrival_by_local = (
        f"{resolved_date['start_date']}T{arrival_time}"
    )

    estimate = fetch_route(origin, destination)

    route_plan = build_route_plan(
        origin=origin,
        destination=destination,
        arrival_by_local=arrival_by_local,
        estimate=estimate,
        generated_at=generated_at,
    )

    # Keep the provider response as source evidence.
    raw = json.dumps(
        {
            "provider": "osrm_demo",
            "origin": origin["entity_key"],
            "destination": destination["entity_key"],
            "estimate": estimate,
            "route_plan": route_plan,
        },
        sort_keys=True,
        ensure_ascii=False,
    )

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    record = TripSourceRecord(
        trip_id=trip_id,
        kind="route_estimate",
        source_id="osrm_demo:driving",
        received_at=generated_at,
        issued_at=generated_at,
        raw=raw,
        raw_sha256=digest,
        normalized_sha256=digest,
    )

    session.add(record)
    session.flush()

    session.add(
        TripObservation(
            trip_id=trip_id,
            record_id=record.id,
            item_id=item.id,
            field="route_plan",
            value=route_plan,
            evidence=raw,
            revision=item.current_revision,
            extractor="resolver:osrm",
        )
    )

    item.details = {
        **details,
        "route_plan": route_plan,
    }

    session.flush()

    return route_plan
