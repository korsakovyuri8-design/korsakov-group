"""Privacy-light, write-only telemetry. Never reads or changes trip state."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.db.models import RevenueClick, RevenuePageView

MAX_BODY_BYTES = 2048
RATE_LIMIT = 60
VIEW_RATE_LIMIT = 120
RATE_WINDOW = 60.0
# Parity with the frontend catalog/policy is enforced by test_revenue_clicks.
ROUTE_SLUGS = frozenset((
    "podgorica-kotor", "tivat-airport-kotor", "podgorica-airport-kotor",
    "podgorica-airport-budva", "tivat-airport-budva", "dubrovnik-airport-kotor",
    "podgorica-budva", "tashkent-airport-tashkent", "tashkent-airport-samarkand",
))
UTM_VALUES = {
    "utm_source": ["instagram", "tiktok", "youtube", "threads", "google", "bing", "linkedin", "facebook", "newsletter", "direct"],
    "utm_medium": ["organic", "social", "shorts", "email", "cpc", "referral"],
    "utm_campaign": ["montenegro_launch", "uzbekistan_launch", "revenue_r2b", "rollout_smoke"],
    "utm_term": ["airport_transfer", "private_transfer", "montenegro_transfer", "uzbekistan_transfer"],
}
UTM_CONTENT_PATTERN = r"(?:reel|post|story|short|video|link)_[0-9]{1,3}"


class RevenueAccessLogFilter(logging.Filter):
    """Uvicorn access tuples contain client IP and the raw query. Drop this path.

    Nginx has the matching access_log off rule. Application logs never receive
    the payload. Do not format a record (or interpolate SQL errors) to filter it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3
                    and isinstance(args[2], str)
                    and args[2].split("?", 1)[0].rstrip("/") in {"/revenue/click", "/revenue/view"})


def suppress_revenue_access_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RevenueAccessLogFilter) for f in logger.filters):
        logger.addFilter(RevenueAccessLogFilter())


class ClickPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["kiwitaxi", "discovercars", "viator", "concierge"]
    placement: Literal["missing_transport", "route_landing", "revenue_panel"]
    route_slug: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None

    @field_validator("route_slug")
    @classmethod
    def known_route(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > 80 or value not in ROUTE_SLUGS:
            raise ValueError("Use a canonical catalog slug.")
        return value

    @field_validator("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
    @classmethod
    def campaign_label(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        # A charset filter alone still permits names, phone numbers and tokens.
        # Only reviewed, non-personal campaign vocabulary is stored.
        if len(value) > 48:
            raise ValueError("Invalid campaign label.")
        valid = (re.fullmatch(UTM_CONTENT_PATTERN, value) if info.field_name == "utm_content"
                 else value in UTM_VALUES[info.field_name])
        if not valid:
            raise ValueError("Invalid campaign label.")
        return value

    @model_validator(mode="after")
    def landing_has_route(self):
        if self.placement == "route_landing" and self.route_slug is None:
            raise ValueError("A landing activation requires its route.")
        return self


class ViewPayload(BaseModel):
    """Only public catalog slug and reviewed campaign labels; no identity."""

    model_config = ConfigDict(extra="forbid", strict=True)
    route_slug: str
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None

    @field_validator("route_slug")
    @classmethod
    def known_route(cls, value: str) -> str:
        if len(value) > 80 or value not in ROUTE_SLUGS:
            raise ValueError("Use a canonical catalog slug.")
        return value

    @field_validator("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
    @classmethod
    def campaign_label(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        if len(value) > 48:
            raise ValueError("Invalid campaign label.")
        valid = (re.fullmatch(UTM_CONTENT_PATTERN, value) if info.field_name == "utm_content"
                 else value in UTM_VALUES[info.field_name])
        if not valid:
            raise ValueError("Invalid campaign label.")
        return value


class ClickLimiter:
    """Bounded process-wide window; stores times only, never visitor identity.

    Multi-worker aggregate limits belong at the proxy. Invalid requests also
    consume capacity. Best-effort telemetry may be dropped under load.
    """

    def __init__(self, clock: Callable[[], float], limit: int = RATE_LIMIT):
        self.clock = clock
        self.limit = limit
        self.hits: deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = self.clock()
            while self.hits and now - self.hits[0] >= RATE_WINDOW:
                self.hits.popleft()
            if len(self.hits) >= self.limit:
                return False
            self.hits.append(now)
            return True


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def revenue_router(factory: sessionmaker[Session], clock: Callable[[], float],
                   utcnow: Callable[[], datetime]) -> APIRouter:
    suppress_revenue_access_logs()
    router = APIRouter()
    limiter = ClickLimiter(clock)
    view_limiter = ClickLimiter(clock, VIEW_RATE_LIMIT)

    def response(status: int, message: str) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        if status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(status_code=status, content={"message": message}, headers=headers)

    def persist(payload: ClickPayload) -> str:
        with factory() as db:
            event = RevenueClick(**payload.model_dump(), created_at=utcnow())
            db.add(event)
            db.commit()  # Return success only after commit. No automatic retry.
            return event.id

    @router.post("/revenue/click")
    async def click(request: Request):
        if not limiter.allow():
            return response(429, "Too many activations. Try later.")
        # No CORS. Reject browser cross-site writes, including same-site subdomains.
        origin = request.headers.get("origin")
        if (request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
                or (origin is not None and origin != str(request.base_url).rstrip("/"))):
            return response(403, "Same-origin requests only.")
        if request.url.query:
            return response(400, "Query parameters are not accepted.")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return response(415, "Send application/json.")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return response(415, "Compressed payloads are not accepted.")
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit():
                return response(400, "Invalid content length.")
            if len(length) > 8 or int(length) > MAX_BODY_BYTES:
                return response(413, "Payload too large.")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return response(413, "Payload too large.")
            body.extend(chunk)
        try:
            data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            return response(400, "Invalid JSON.")
        try:
            payload = ClickPayload.model_validate(data)
        except ValidationError:
            # Never echo values or Pydantic's input-bearing error details.
            return response(422, "Invalid activation fields.")
        try:
            event_id = await run_in_threadpool(persist, payload)
        except SQLAlchemyError:
            # DB exception strings may contain SQL parameters: do not log them.
            return response(503, "Telemetry unavailable.")
        return JSONResponse(status_code=201, content={"ok": True, "id": event_id},
                            headers={"Cache-Control": "no-store"})

    def persist_view(payload: ViewPayload) -> str:
        with factory() as db:
            event = RevenuePageView(**payload.model_dump(), created_at=utcnow())
            db.add(event)
            db.commit()
            return event.id

    @router.post("/revenue/view")
    async def view_event(request: Request):
        if not view_limiter.allow():
            return response(429, "Too many views. Try later.")
        origin = request.headers.get("origin")
        if (request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
                or (origin is not None and origin != str(request.base_url).rstrip("/"))):
            return response(403, "Same-origin requests only.")
        if request.url.query:
            return response(400, "Query parameters are not accepted.")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return response(415, "Send application/json.")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return response(415, "Compressed payloads are not accepted.")
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit():
                return response(400, "Invalid content length.")
            if len(length) > 8 or int(length) > MAX_BODY_BYTES:
                return response(413, "Payload too large.")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return response(413, "Payload too large.")
            body.extend(chunk)
        try:
            data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            return response(400, "Invalid JSON.")
        try:
            payload = ViewPayload.model_validate(data)
        except ValidationError:
            return response(422, "Invalid view fields.")
        try:
            event_id = await run_in_threadpool(persist_view, payload)
        except SQLAlchemyError:
            return response(503, "Telemetry unavailable.")
        return JSONResponse(status_code=201, content={"ok": True, "id": event_id},
                            headers={"Cache-Control": "no-store"})

    return router
