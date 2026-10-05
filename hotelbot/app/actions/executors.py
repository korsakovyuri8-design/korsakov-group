"""Action executors: *how* an action reaches the party that fulfils it.

* `StaffQueueExecutor` - the action lands in the staff queue (SUBMITTED);
  a human moves it on via the staff API.
* `WebhookExecutor` - POSTs the action to an external system declared in the
  property's capabilities (a transport partner, a property's own tooling, an
  automation platform). The response decides the status. Timeouts, HTTP
  errors and malformed responses become FAILED - never an assumed success.

Executors return an `ExecutionOutcome`; they never write action state
themselves (ActionService does, with an audit event).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.db.models import Action, ActionStatus


class ExecutionOutcome(BaseModel):
    status: ActionStatus           # SUBMITTED | ACCEPTED | REJECTED | FAILED
    external_ref: str | None = None
    error: str | None = None
    result: dict[str, Any] = {}


class ActionExecutor(Protocol):
    name: str

    def submit(self, action: Action, config: dict[str, Any]) -> ExecutionOutcome: ...


class StaffQueueExecutor:
    name = "staff"

    def submit(self, action: Action, config: dict[str, Any]) -> ExecutionOutcome:
        return ExecutionOutcome(status=ActionStatus.SUBMITTED)


_WEBHOOK_STATUS = {
    "accepted": ActionStatus.ACCEPTED,
    "confirmed": ActionStatus.ACCEPTED,
    "received": ActionStatus.SUBMITTED,
    "pending": ActionStatus.SUBMITTED,
    "submitted": ActionStatus.SUBMITTED,
    "rejected": ActionStatus.REJECTED,
}


class WebhookExecutor:
    """Contract with the external system:

    request:  POST <url>  {"action_id", "action_type", "property_id", "summary", "params"}
              header X-HotelBot-Signature: sha256=<HMAC of body> (if secret_env is set)
    response: 2xx {"status": "accepted"|"received"|"rejected", "reference": "...", "message": "..."}
    """

    name = "webhook"
    DEFAULT_TIMEOUT = 10.0

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client()

    def submit(self, action: Action, config: dict[str, Any]) -> ExecutionOutcome:
        url = config.get("url")
        if not url:
            return ExecutionOutcome(status=ActionStatus.FAILED, error="webhook url not configured")
        body = json.dumps({
            "action_id": action.id,
            "action_type": action.action_type,
            "property_id": action.property_id,
            "summary": action.summary,
            "params": action.params,
        }, ensure_ascii=False, default=str).encode()
        headers = {"Content-Type": "application/json"}
        secret = os.environ.get(config["secret_env"]) if config.get("secret_env") else None
        if secret:
            headers["X-HotelBot-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            resp = self._client.post(url, content=body, headers=headers,
                                     timeout=float(config.get("timeout", self.DEFAULT_TIMEOUT)))
        except httpx.TimeoutException:
            return ExecutionOutcome(status=ActionStatus.FAILED, error="timeout")
        except httpx.HTTPError as exc:
            return ExecutionOutcome(status=ActionStatus.FAILED, error=f"transport error: {type(exc).__name__}")
        if resp.status_code >= 300:
            return ExecutionOutcome(status=ActionStatus.FAILED, error=f"HTTP {resp.status_code}")
        try:
            data = resp.json()
            status = _WEBHOOK_STATUS[str(data["status"]).lower()]
        except (ValueError, KeyError, TypeError):
            return ExecutionOutcome(status=ActionStatus.FAILED, error="malformed response")
        reference = data.get("reference")
        return ExecutionOutcome(
            status=status,
            external_ref=str(reference)[:255] if reference else None,
            result={k: v for k, v in data.items() if k in ("status", "reference", "message")},
        )


class ExecutorRegistry:
    def __init__(self, executors: list[ActionExecutor]) -> None:
        self._by_name = {e.name: e for e in executors}

    def get(self, name: str) -> ActionExecutor | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return sorted(self._by_name)


def default_executors(http_client: httpx.Client | None = None) -> ExecutorRegistry:
    return ExecutorRegistry([StaffQueueExecutor(), WebhookExecutor(http_client)])
