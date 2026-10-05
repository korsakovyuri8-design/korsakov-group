"""Locale-neutral formatting of transaction details shown to the guest.

"label: value" lists avoid grammatical agreement problems in Montenegrin and
Russian; every value shown is exactly what will be submitted, so the guest's
consent covers it."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.clock import as_utc
from app.transactions.catalog import SERVICE_CATALOG, localized
from app.transactions.slots import PROPERTY


def format_price(amount: Decimal, currency: str) -> str:
    return f"{Decimal(amount):.2f} {currency}"


def format_when(value: str | datetime, tz: str) -> str:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M")


def format_until(value: datetime, tz: str, now: datetime) -> str:
    local, today = as_utc(value).astimezone(ZoneInfo(tz)), now.astimezone(ZoneInfo(tz)).date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%d.%m.%Y %H:%M")


def format_value(kind: str, value: Any, tz: str, property_name: str) -> str:
    if value == PROPERTY:
        return property_name
    if kind == "datetime" and value:
        return format_when(value, tz)
    return str(value)


def format_summary(service_type: str, details: dict[str, Any], locale: str, tz: str, property_name: str,
                   *, with_label: bool = True) -> str:
    spec = SERVICE_CATALOG[service_type]
    parts = [localized(spec.labels, locale)] if with_label else []
    for f in spec.fields:
        if details.get(f.key) is not None:
            parts.append(f"{localized(f.labels, locale)}: {format_value(f.kind, details[f.key], tz, property_name)}")
    # Labels are localized (cnr-Cyrl transliterated); guest-provided values
    # such as place names are kept exactly as the guest wrote them.
    return "; ".join(parts)
