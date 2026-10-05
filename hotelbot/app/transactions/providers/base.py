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

OutcomeStatus = Literal["received", "accepted", "rejected", "in_progress", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class QuoteRequest:
    service_type: str
    details: dict[str, Any]
    locale: str = "en"


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
