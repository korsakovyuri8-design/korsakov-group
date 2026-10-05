"""Deterministic in-process provider for development, tests and the
evaluation harness. Behaves like a well-behaved real provider:

* prices from configuration (base + per extra person + night surcharge);
* quotes expire after `validity_minutes`;
* submit is idempotent on the idempotency key (same key -> same booking);
* configurable outcomes to exercise every failure path.

config:
  currency: EUR
  pricing: {base: 30, per_extra_person: 5, night_surcharge: 10}
  validity_minutes: 15
  conditions: "Free cancellation up to 2 hours before pickup."
  behavior:
    quote:  ok | timeout | unavailable
    submit: accepted | received | rejected | timeout | unavailable | invalid | auth | flaky:N
            (flaky:N = N timeouts, then accepted)
    cancel: cancelled | refused | timeout
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.clock import Clock
from app.transactions.providers.base import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderOutcome,
    ProviderTimeout,
    ProviderUnavailable,
    QuoteRequest,
    QuoteResult,
    SubmitRequest,
)


class MockExternalProvider:
    def __init__(self, slug: str, config: dict[str, Any], clock: Clock) -> None:
        self.slug = slug
        self.config = config
        self.clock = clock
        self.behavior: dict[str, str] = dict(config.get("behavior") or {})
        self.bookings: dict[str, dict[str, Any]] = {}     # idempotency key -> booking
        self.submit_calls = 0
        self.cancel_calls = 0
        self._flaky_failures = 0

    # ----------------------------------------------------------------- quote
    def request_quote(self, request: QuoteRequest) -> QuoteResult:
        mode = self.behavior.get("quote", "ok")
        if mode == "timeout":
            raise ProviderTimeout("mock quote timeout")
        if mode == "unavailable":
            raise ProviderUnavailable("mock quote unavailable")
        pricing = self.config.get("pricing") or {}
        amount = Decimal(str(pricing.get("base", 30)))
        people = int(request.details.get("party_size") or 1)
        amount += Decimal(str(pricing.get("per_extra_person", 0))) * max(0, people - 1)
        when = request.details.get("pickup_time") or request.details.get("start_time")
        if when:
            hour = datetime.fromisoformat(when).hour
            if hour >= 22 or hour < 6:
                amount += Decimal(str(pricing.get("night_surcharge", 0)))
        digest = hashlib.sha1(repr(sorted(request.details.items())).encode()).hexdigest()[:8].upper()
        return QuoteResult(
            amount=amount.quantize(Decimal("0.01")),
            currency=self.config.get("currency", "EUR"),
            description=self.config.get("description", request.service_type.replace("_", " ")),
            conditions=self.config.get("conditions"),
            valid_until=self.clock.now() + timedelta(minutes=int(self.config.get("validity_minutes", 15))),
            provider_reference=f"Q-{digest}",
        )

    # ---------------------------------------------------------------- submit
    def submit(self, request: SubmitRequest) -> ProviderOutcome:
        self.submit_calls += 1
        existing = self.bookings.get(request.idempotency_key)
        if existing is not None:   # provider-side idempotency
            return ProviderOutcome(status=existing["status"], reference=existing["reference"], message="duplicate")
        mode = self.behavior.get("submit", "accepted")
        if mode.startswith("flaky:"):
            if self._flaky_failures < int(mode.split(":")[1]):
                self._flaky_failures += 1
                raise ProviderTimeout("mock transient timeout")
            mode = "accepted"
        if mode == "timeout":
            raise ProviderTimeout("mock submit timeout")
        if mode == "unavailable":
            raise ProviderUnavailable("mock provider HTTP 503")
        if mode == "invalid":
            raise ProviderInvalidRequest("mock provider rejected the request format")
        if mode == "auth":
            raise ProviderAuthError("mock provider credentials rejected")
        reference = "MOCK-" + hashlib.sha1(request.idempotency_key.encode()).hexdigest()[:8].upper()
        status = {"accepted": "accepted", "received": "received", "rejected": "rejected"}[mode]
        if status != "rejected":
            self.bookings[request.idempotency_key] = {"reference": reference, "status": status,
                                                      "details": request.details}
        return ProviderOutcome(status=status, reference=reference,  # type: ignore[arg-type]
                               message="no vehicles available" if status == "rejected" else None)

    # ---------------------------------------------------------------- cancel
    def cancel(self, reference: str, idempotency_key: str) -> ProviderOutcome:
        self.cancel_calls += 1
        mode = self.behavior.get("cancel", "cancelled")
        if mode == "timeout":
            raise ProviderTimeout("mock cancel timeout")
        if mode == "refused":
            return ProviderOutcome(status="rejected", reference=reference, message="too late to cancel")
        for booking in self.bookings.values():
            if booking["reference"] == reference:
                booking["status"] = "cancelled"
        return ProviderOutcome(status="cancelled", reference=reference)

    def get_status(self, reference: str) -> ProviderOutcome:
        for booking in self.bookings.values():
            if booking["reference"] == reference:
                return ProviderOutcome(status=booking["status"], reference=reference)
        raise ProviderInvalidRequest(f"unknown reference {reference}")

    @property
    def active_bookings(self) -> int:
        return sum(1 for b in self.bookings.values() if b["status"] != "cancelled")
