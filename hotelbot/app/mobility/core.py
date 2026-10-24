"""Atlas Mobility: multimodal journey search above transaction providers.

This layer does NOT invent offers, prices or availability.

Journey intent:
    where the traveller is
    where they need to be
    when they must arrive
    party size

Mobility mode:
    private transfer
    scheduled bus
    rental car
    rail

Transaction providers later fulfil those modes.

MODEL MAY PROPOSE.
CODE DECIDES whether a mode has provider/network evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from zoneinfo import ZoneInfo


class MobilityMode(str, Enum):
    PRIVATE_TRANSFER = "private_transfer"
    SCHEDULED_BUS = "scheduled_bus"
    RENTAL_CAR = "rental_car"
    RAIL = "rail"


class ModeStatus(str, Enum):
    APPLICABLE = "applicable"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class MobilityModeSpec:
    key: MobilityMode
    service_type: str
    fixed_details: Mapping[str, Any] = field(
        default_factory=dict,
    )
    requires_provider_selection: bool = False


MODE_SPECS: dict[MobilityMode, MobilityModeSpec] = {
    MobilityMode.PRIVATE_TRANSFER: MobilityModeSpec(
        key=MobilityMode.PRIVATE_TRANSFER,
        service_type="intercity_transfer",
    ),
    MobilityMode.SCHEDULED_BUS: MobilityModeSpec(
        key=MobilityMode.SCHEDULED_BUS,
        service_type="bus_ticket",
        requires_provider_selection=True,
    ),
    MobilityMode.RENTAL_CAR: MobilityModeSpec(
        key=MobilityMode.RENTAL_CAR,
        service_type="rental",
        fixed_details={
            "category": "car",
            "quantity": 1,
        },
        requires_provider_selection=True,
    ),
    MobilityMode.RAIL: MobilityModeSpec(
        key=MobilityMode.RAIL,
        service_type="rail_ticket",
        requires_provider_selection=True,
    ),
}


@dataclass(frozen=True)
class MobilityRequest:
    origin: str
    destination: str
    arrive_by_local: datetime
    party_size: int | None
    timezone: str
    leave_by_local: datetime | None = None
    origin_entity_key: str | None = None
    destination_entity_key: str | None = None

    def __post_init__(self) -> None:
        if not self.origin.strip():
            raise ValueError("origin is required")
        if not self.destination.strip():
            raise ValueError("destination is required")
        if (
            self.party_size is not None
            and self.party_size < 1
        ):
            raise ValueError("party_size must be positive")
        if self.arrive_by_local.tzinfo is None:
            raise ValueError(
                "arrive_by_local must be timezone-aware"
            )
        if (
            self.leave_by_local is not None
            and self.leave_by_local.tzinfo is None
        ):
            raise ValueError(
                "leave_by_local must be timezone-aware"
            )


@dataclass(frozen=True)
class ModeEvidence:
    """Evidence supplied by marketplace/network adapters.

    provider_available:
        Atlas has an actual provider capable of serving this mode.

    network_applicable:
        True  = network explicitly supports the journey.
        False = network explicitly says this journey cannot be served.
        None  = no network conclusion yet.

    Absence of evidence never becomes an offer.
    """

    provider_available: bool = False
    network_applicable: bool | None = None
    source: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ModeAssessment:
    mode: MobilityMode
    service_type: str
    status: ModeStatus
    reason: str
    evidence_source: str | None = None


def assess_mode(
    mode: MobilityMode,
    evidence: ModeEvidence | None,
) -> ModeAssessment:
    spec = MODE_SPECS[mode]

    if evidence is not None and evidence.network_applicable is False:
        return ModeAssessment(
            mode=mode,
            service_type=spec.service_type,
            status=ModeStatus.NOT_APPLICABLE,
            reason=evidence.reason or "network_not_applicable",
            evidence_source=evidence.source,
        )

    if evidence is None or not evidence.provider_available:
        return ModeAssessment(
            mode=mode,
            service_type=spec.service_type,
            status=ModeStatus.UNAVAILABLE,
            reason=(
                evidence.reason
                if evidence and evidence.reason
                else "no_provider_evidence"
            ),
            evidence_source=evidence.source if evidence else None,
        )

    return ModeAssessment(
        mode=mode,
        service_type=spec.service_type,
        status=ModeStatus.APPLICABLE,
        reason=evidence.reason or "provider_available",
        evidence_source=evidence.source,
    )


def assess_modes(
    evidence: Mapping[MobilityMode, ModeEvidence],
) -> tuple[ModeAssessment, ...]:
    return tuple(
        assess_mode(
            mode,
            evidence.get(mode),
        )
        for mode in MobilityMode
    )


def search_constraints(
    request: MobilityRequest,
    mode: MobilityMode,
) -> dict[str, Any]:
    """Build search constraints, NOT a Quote and NOT a booking.

    Scheduled bus/rail still need a concrete provider itinerary before a
    transaction Quote can be created.
    """

    spec = MODE_SPECS[mode]

    common = {
        "origin": request.origin,
        "destination": request.destination,
        "arrive_by": request.arrive_by_local.isoformat(),
    }

    if request.party_size is not None:
        common["party_size"] = request.party_size

    if mode == MobilityMode.PRIVATE_TRANSFER:
        if request.leave_by_local is None:
            raise ValueError(
                "private transfer requires leave_by_local"
            )

        details = {
            "pickup": request.origin,
            "destination": request.destination,
            "pickup_time": request.leave_by_local.isoformat(),
        }

        if request.party_size is not None:
            details["party_size"] = request.party_size

        return details

    if mode == MobilityMode.RENTAL_CAR:
        if request.leave_by_local is None:
            raise ValueError(
                "rental car requires leave_by_local"
            )

        return {
            **common,
            **dict(spec.fixed_details),
            "start_time": request.leave_by_local.isoformat(),
        }

    # Bus / rail search by journey + arrival deadline.
    # departure_time is added only after the provider returns a concrete
    # itinerary selected for quoting.
    return common



def _local_datetime(value: str, timezone: str) -> datetime:
    """Journey route times are local wall-clock values.

    If an offset is already present, preserve the instant and express it in
    the route timezone. If no offset is present, attach the route timezone.
    """
    dt = datetime.fromisoformat(value)
    zone = ZoneInfo(timezone)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)

    return dt.astimezone(zone)


def request_from_plan_details(
    details: Mapping[str, Any],
    *,
    require_party_size: bool = True,
) -> MobilityRequest:
    """Build mobility intent from deterministic Journey Graph data."""

    route = details.get("route_plan")

    if not isinstance(route, Mapping):
        raise ValueError("route_plan_missing")

    origin_obj = route.get("origin")
    destination_obj = route.get("destination")

    origin = (
        origin_obj.get("name")
        if isinstance(origin_obj, Mapping)
        else None
    ) or details.get("origin_text")

    destination = (
        destination_obj.get("name")
        if isinstance(destination_obj, Mapping)
        else None
    ) or details.get("place")

    arrive_raw = route.get("arrive_by_local")
    leave_raw = route.get("leave_by_local")
    timezone = route.get("timezone") or "UTC"
    party_size = details.get("party_size")

    if not origin:
        raise ValueError("origin_missing")

    if not destination:
        raise ValueError("destination_missing")

    if not arrive_raw:
        raise ValueError("arrive_by_missing")

    if party_size is not None and (
        not isinstance(party_size, int)
        or isinstance(party_size, bool)
        or party_size < 1
    ):
        raise ValueError("party_size_invalid")

    if party_size is None and require_party_size:
        raise ValueError("party_size_missing")

    return MobilityRequest(
        origin=str(origin),
        destination=str(destination),
        arrive_by_local=_local_datetime(
            str(arrive_raw),
            str(timezone),
        ),
        leave_by_local=(
            _local_datetime(
                str(leave_raw),
                str(timezone),
            )
            if leave_raw
            else None
        ),
        party_size=party_size,
        timezone=str(timezone),
        origin_entity_key=(
            str(origin_obj["entity_key"])
            if isinstance(origin_obj, Mapping) and origin_obj.get("entity_key") is not None
            else None
        ),
        destination_entity_key=(
            str(destination_obj["entity_key"])
            if isinstance(destination_obj, Mapping) and destination_obj.get("entity_key") is not None
            else None
        ),
    )
