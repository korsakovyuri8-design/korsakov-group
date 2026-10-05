"""Provider adapter interface - independent of any transport (HTTP, SDK,
email, manual). The transaction core only sees these types.

Errors are classified so the job runner can decide what to do:

    ProviderTimeout        retryable   (the provider may or may not have acted;
                                        safe to retry because submit carries an
                                        idempotency key)
    ProviderUnavailable    retryable   (5xx, 429, network, malformed response)
    ProviderInvalidRequest permanent   (the request itself is wrong)
    ProviderAuthError      permanent   (credentials/configuration - needs a human)

A *business* rejection ("no drivers available") is not an error: it is an
outcome with status "rejected".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

# "accepted_conditional": accepted SUBJECT TO a condition (e.g. weather) -
# never reported to the guest as a confirmed booking.
OutcomeStatus = Literal["received", "accepted", "accepted_conditional", "rejected", "in_progress", "completed",
                        "failed", "cancelled"]


@dataclass(frozen=True)
class QuoteRequest:
    service_type: str
    details: dict[str, Any]
    locale: str = "en"
    # The catalogued offering being priced (slug, pricing, attributes), when
    # the provider publishes one. Real providers receive only its reference.
    offering: dict[str, Any] | None = None


@dataclass(frozen=True)
class QuoteResult:
    amount: Decimal
    currency: str
    description: str
    valid_until: datetime
    conditions: str | None = None
    provider_reference: str | None = None


@dataclass(frozen=True)
class SubmitRequest:
    idempotency_key: str
    service_type: str
    details: dict[str, Any]
    quote_reference: str | None
    amount: Decimal
    currency: str
    customer_reference: str            # opaque id, no guest PII
    offering_ref: str | None = None
    # Change of an existing booking (provider-supported modification).
    modifies_reference: str | None = None


@dataclass(frozen=True)
class ProviderOutcome:
    status: OutcomeStatus
    reference: str | None = None
    message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    retryable: bool = False
    kind: str = "error"


class ProviderTimeout(ProviderError):
    retryable, kind = True, "timeout"


class ProviderUnavailable(ProviderError):
    retryable, kind = True, "unavailable"


class ProviderInvalidRequest(ProviderError):
    retryable, kind = False, "invalid_request"


class ProviderAuthError(ProviderError):
    retryable, kind = False, "auth"


class ProviderAdapter(Protocol):
    def request_quote(self, request: QuoteRequest) -> QuoteResult: ...

    def submit(self, request: SubmitRequest) -> ProviderOutcome: ...

    def cancel(self, reference: str, idempotency_key: str) -> ProviderOutcome: ...

    def get_status(self, reference: str) -> ProviderOutcome: ...

    # Optional: providers with config `supports_modification: true` implement
    # modify(); others get cancel + new booking.
    # def modify(self, request: SubmitRequest) -> ProviderOutcome: ...
