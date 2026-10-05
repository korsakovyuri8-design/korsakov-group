"""Generic HTTP/JSON provider adapter.

Contract (documented in docs/PROVIDER_INTEGRATION.md):

    POST {base_url}/quotes                  -> 200 {amount, currency, description, valid_until, conditions?, reference?}
    POST {base_url}/bookings                -> 200/201/202 {status: received|accepted|rejected, reference, message?}
         header Idempotency-Key: <key>         (same key => same booking, no duplicate)
    POST {base_url}/bookings/{ref}/cancel   -> 200 {status: cancelled|rejected, message?}
    GET  {base_url}/bookings/{ref}          -> 200 {status, reference}

Every request carries X-HotelBot-Timestamp and
X-HotelBot-Signature: sha256=HMAC(secret, "<timestamp>.<body>") when
`secret_env` names an environment variable; `token_env` adds a bearer token.
Secrets never live in configuration - only the variable names.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

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

_STATUSES = {"received", "accepted", "rejected", "in_progress", "completed", "failed", "cancelled"}


class WebhookExternalProvider:
    def __init__(self, slug: str, config: dict[str, Any], clock: Clock, client: httpx.Client | None = None) -> None:
        self.slug = slug
        self.config = config
        self.clock = clock
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        self.timeout = float(config.get("timeout", 10))
        self._client = client or httpx.Client()

    # -------------------------------------------------------------- plumbing
    def _headers(self, body: bytes, idempotency_key: str | None) -> dict[str, str]:
        if not self.base_url:
            raise ProviderAuthError("provider base_url not configured")
        headers = {"Content-Type": "application/json"}
        ts = str(int(self.clock.now().timestamp()))
        headers["X-HotelBot-Timestamp"] = ts
        secret_env = self.config.get("secret_env")
        if secret_env:
            secret = os.environ.get(secret_env)
            if not secret:
                raise ProviderAuthError(f"environment variable {secret_env} is not set")
            headers["X-HotelBot-Signature"] = "sha256=" + hmac.new(
                secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        token_env = self.config.get("token_env")
        if token_env:
            token = os.environ.get(token_env)
            if not token:
                raise ProviderAuthError(f"environment variable {token_env} is not set")
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None,
              idempotency_key: str | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}, ensure_ascii=False, default=str).encode() if method == "POST" else b""
        headers = self._headers(body, idempotency_key)
        try:
            resp = self._client.request(method, self.base_url + path, content=body or None, headers=headers,
                                        timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{method} {path} timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"{method} {path}: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"HTTP {resp.status_code}")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderInvalidRequest(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError
        except ValueError as exc:
            raise ProviderUnavailable("malformed provider response") from exc
        return data

    @staticmethod
    def _outcome(data: dict[str, Any]) -> ProviderOutcome:
        status = str(data.get("status", "")).lower()
        if status not in _STATUSES:
            raise ProviderUnavailable(f"unknown provider status {status!r}")
        ref = data.get("reference")
        return ProviderOutcome(status=status, reference=str(ref)[:255] if ref else None,  # type: ignore[arg-type]
                               message=data.get("message"), raw=data)

    # ------------------------------------------------------------- interface
    def request_quote(self, request: QuoteRequest) -> QuoteResult:
        data = self._call("POST", "/quotes", {"service_type": request.service_type, "details": request.details,
                                              "locale": request.locale})
        try:
            return QuoteResult(
                amount=Decimal(str(data["amount"])).quantize(Decimal("0.01")),
                currency=str(data["currency"])[:3].upper(),
                description=str(data.get("description") or request.service_type),
                conditions=data.get("conditions"),
                valid_until=datetime.fromisoformat(str(data["valid_until"])),
                provider_reference=data.get("reference"),
            )
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ProviderUnavailable("malformed quote") from exc

    def submit(self, request: SubmitRequest) -> ProviderOutcome:
        return self._outcome(self._call("POST", "/bookings", {
            "service_type": request.service_type,
            "details": request.details,
            "quote_reference": request.quote_reference,
            "amount": str(request.amount),
            "currency": request.currency,
            "customer_reference": request.customer_reference,
        }, idempotency_key=request.idempotency_key))

    def cancel(self, reference: str, idempotency_key: str) -> ProviderOutcome:
        return self._outcome(self._call("POST", f"/bookings/{reference}/cancel", {}, idempotency_key=idempotency_key))

    def get_status(self, reference: str) -> ProviderOutcome:
        return self._outcome(self._call("GET", f"/bookings/{reference}"))
