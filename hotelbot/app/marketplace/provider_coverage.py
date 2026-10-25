"""Deterministic provider service-coverage evidence.

Geography answers "where is this place?". ProviderCoverage answers a
different question: "does this provider have verified authority/capability
to expose THIS service in THIS scope right now?"

No row means no coverage. Stale, malformed, synthetic-without-opt-in, or
capability-missing rows fail closed. This module does not infer coverage from
ExternalProvider.region.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExternalProvider, ProviderCoverage


VALID_SCOPE_KINDS = frozenset({"area", "route"})
VALID_INTEGRATION_MODES = frozenset(
    {"live_api", "affiliate", "lead", "sandbox"}
)
VALID_VERIFICATION_TYPES = frozenset(
    {
        "provider_contract",
        "provider_api",
        "provider_document",
        "operator_verified",
        "synthetic_fixture",
    }
)
VALID_CAPABILITIES = frozenset(
    {"discover", "quote", "book", "lookup", "change", "cancel"}
)

MODE_CAPABILITIES = {
    "live_api": VALID_CAPABILITIES,
    "affiliate": frozenset({"discover"}),
    "lead": frozenset({"discover"}),
    "sandbox": VALID_CAPABILITIES,
}

# A single geographic area does not prove an inter-city route.
ROUTE_ONLY_SERVICES = frozenset({"intercity_transfer"})


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _provider_declares_service(
    provider: ExternalProvider,
    service_type: str,
) -> bool:
    services = provider.services
    if not isinstance(services, dict):
        return False
    declared = services.get("service_types")
    return isinstance(declared, list) and service_type in declared


def _well_formed(row: ProviderCoverage) -> bool:
    if row.scope_kind not in VALID_SCOPE_KINDS:
        return False
    if row.integration_mode not in VALID_INTEGRATION_MODES:
        return False
    if row.verification_type not in VALID_VERIFICATION_TYPES:
        return False
    if not isinstance(row.capabilities, dict):
        return False
    if not isinstance(row.evidence, dict):
        return False
    if not isinstance(row.evidence_source, str) or not row.evidence_source.strip():
        return False
    if row.verified_at is None or row.valid_until is None:
        return False

    if row.integration_mode == "sandbox":
        if row.verification_type != "synthetic_fixture":
            return False
    elif row.verification_type == "synthetic_fixture":
        return False

    if row.scope_kind == "area":
        return (
            bool(row.area_code)
            and row.origin_entity_key is None
            and row.destination_entity_key is None
        )

    return (
        row.area_code is None
        and bool(row.origin_entity_key)
        and bool(row.destination_entity_key)
    )


def _fresh(row: ProviderCoverage, now: datetime) -> bool:
    now_utc = _aware(now)
    verified = _aware(row.verified_at)
    valid_until = _aware(row.valid_until)

    if verified > now_utc:
        return False
    if valid_until <= verified or valid_until <= now_utc:
        return False

    if row.valid_from is not None:
        valid_from = _aware(row.valid_from)
        if valid_from >= valid_until or valid_from > now_utc:
            return False

    return True


def _has_capability(
    row: ProviderCoverage,
    capability: str,
) -> bool:
    if capability not in VALID_CAPABILITIES:
        return False
    if capability not in MODE_CAPABILITIES[row.integration_mode]:
        return False
    return row.capabilities.get(capability) is True


def _scope_matches(
    row: ProviderCoverage,
    *,
    service_type: str,
    area_codes: frozenset[str],
    origin_entity_key: str | None,
    destination_entity_key: str | None,
) -> bool:
    if row.scope_kind == "area":
        if service_type in ROUTE_ONLY_SERVICES:
            return False
        return row.area_code in area_codes

    if not origin_entity_key or not destination_entity_key:
        return False

    direct = (
        row.origin_entity_key == origin_entity_key
        and row.destination_entity_key == destination_entity_key
    )
    if direct:
        return True

    if row.directional:
        return False

    return (
        row.origin_entity_key == destination_entity_key
        and row.destination_entity_key == origin_entity_key
    )


def find_verified_coverage(
    session: Session,
    *,
    provider_id: str,
    service_type: str,
    capability: str,
    now: datetime,
    area_codes: set[str] | frozenset[str] | tuple[str, ...] = (),
    origin_entity_key: str | None = None,
    destination_entity_key: str | None = None,
    allow_sandbox: bool = False,
) -> ProviderCoverage | None:
    """Return one deterministic verified coverage row, or None.

    Exact route coverage outranks area coverage. Missing evidence never falls
    back to ExternalProvider.region and missing capabilities are never inferred.
    """

    provider = session.get(ExternalProvider, provider_id)
    if (
        provider is None
        or not provider.active
        or not _provider_declares_service(provider, service_type)
    ):
        return None

    normalized_areas = frozenset(
        code
        for code in area_codes
        if isinstance(code, str) and code
    )

    rows = list(
        session.scalars(
            select(ProviderCoverage)
            .where(
                ProviderCoverage.provider_id == provider_id,
                ProviderCoverage.service_type == service_type,
                ProviderCoverage.active.is_(True),
            )
            .order_by(ProviderCoverage.coverage_key)
        )
    )

    matches: list[tuple[int, str, ProviderCoverage]] = []

    for row in rows:
        if not _well_formed(row):
            continue
        if row.integration_mode == "sandbox" and not allow_sandbox:
            continue
        if not _fresh(row, now):
            continue
        if not _has_capability(row, capability):
            continue
        if not _scope_matches(
            row,
            service_type=service_type,
            area_codes=normalized_areas,
            origin_entity_key=origin_entity_key,
            destination_entity_key=destination_entity_key,
        ):
            continue

        rank = 0 if row.scope_kind == "route" else 1
        matches.append((rank, row.coverage_key, row))

    if not matches:
        return None

    matches.sort(key=lambda item: (item[0], item[1]))
    return matches[0][2]
