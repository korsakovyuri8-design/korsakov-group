"""Synthetic mobility providers for the Atlas development flow.

NEVER present these as real companies or real availability.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExternalProvider


REGION = "montenegro-beta"
TRANSFER_SLUG = "atlas-sandbox-transfer"


def ensure_transfer_provider(
    session: Session,
) -> ExternalProvider:
    existing = session.scalar(
        select(ExternalProvider).where(
            ExternalProvider.region == REGION,
            ExternalProvider.slug == TRANSFER_SLUG,
        )
    )

    if existing is not None:
        return existing

    provider = ExternalProvider(
        property_id=None,
        region=REGION,
        slug=TRANSFER_SLUG,
        name="Atlas Sandbox Transfer",
        provider_type="transport",
        integration_type="mock",
        active=True,
        services={
            "service_types": [
                "intercity_transfer",
            ],
        },
        profile={
            "synthetic": True,
            "service_area": "Montenegro development sandbox",
        },
        policies={
            "free_cancellation_hours": 2,
        },
        config={
            "currency": "EUR",
            # DEVELOPMENT PRICE. Not a market claim.
            "pricing": {
                "base": 60,
                "per_extra_person": 5,
            },
            "validity_minutes": 15,
            "conditions": (
                "Synthetic development offer. "
                "This is not a real transport booking."
            ),
            "description": "Sandbox private transfer",
        },
    )

    session.add(provider)
    session.flush()

    return provider
