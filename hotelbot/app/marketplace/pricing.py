"""Deterministic price computation from an offering's declared pricing.

Used by the mock adapter (synthetic providers) and for ranking estimates.
A real provider prices its own quotes; the guest only ever sees the price
in the provider's quote.

    pricing:
      model: per_booking | per_person | per_item_day | per_day | per_hour
      amount: 30                     # base / unit price
      included_people: 1             # per_booking: extra people beyond this cost per_extra_person
      per_extra_person: 5
      night_surcharge: 10            # pickup between 22:00 and 06:00 local
      child_seat: 5                  # per child seat
      min_hours: 2                   # per_hour
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

CENT = Decimal("0.01")


def compute(pricing: dict[str, Any], details: dict[str, Any], *, tz: str = "UTC") -> Decimal | None:
    if not pricing or "amount" not in pricing:
        return None
    model = pricing.get("model", "per_booking")
    amount = Decimal(str(pricing["amount"]))
    party = int(details.get("party_size") or 1)
    qty = int(details.get("quantity") or 1)
    days = max(1, int(details.get("days") or 1))
    hours = max(int(pricing.get("min_hours", 1)), int(details.get("hours") or pricing.get("min_hours", 1)))
    if model == "per_person":
        total = amount * party
    elif model == "per_item_day":
        total = amount * qty * days
    elif model == "per_day":
        total = amount * days
    elif model == "per_hour":
        total = amount * hours
    else:  # per_booking
        extra = max(0, party - int(pricing.get("included_people", 1)))
        total = amount + Decimal(str(pricing.get("per_extra_person", 0))) * extra
    if pricing.get("child_seat") and details.get("child_seats"):
        total += Decimal(str(pricing["child_seat"])) * int(details["child_seats"])
    when = details.get("pickup_time")
    if pricing.get("night_surcharge") and when:
        local = datetime.fromisoformat(when).astimezone(ZoneInfo(tz))
        if local.hour >= 22 or local.hour < 6:
            total += Decimal(str(pricing["night_surcharge"]))
    return total.quantize(CENT)


def commission(offering_or_provider: Any, amount: Decimal, *, provider: Any = None) -> dict[str, Any]:
    """Commercial snapshot taken at quote time, for later accounting. Never
    alters `amount`. The figure is POTENTIAL: it is earned only once the
    provider has confirmed (or completed) the booking - see
    app/marketplace/commission.py. Percent of the guest price for paid
    services; a fixed referral/lead fee (or 0) for free bookings such as a
    restaurant table."""
    ctype = getattr(offering_or_provider, "commission_type", None)
    cval = getattr(offering_or_provider, "commission_value", None)
    if ctype is None and provider is not None:      # the offering has no terms of its own: the provider's apply
        ctype, cval = getattr(provider, "commission_type", None), getattr(provider, "commission_value", None)
    out: dict[str, Any] = {"guest_price": str(amount), "commission_type": ctype, "basis": "potential"}
    if ctype == "percent" and cval is not None:
        out["commission"] = str((amount * Decimal(cval) / 100).quantize(CENT))
    elif ctype in ("fixed", "markup") and cval is not None:
        out["commission"] = str(Decimal(cval).quantize(CENT))
    partner = getattr(offering_or_provider, "partner_price", None)
    if partner is not None:
        out["partner_price"] = str(partner)
    return out
