"""Korsakov Atlas - public demo service (Competition Release 1.0).

    GET  /                    the demo page (import first, the 90-second demo second)
    GET  /demo/sample         a new session with the three sample bookings
    POST /demo/import         {session?, text | sample} -> what Atlas understood + the trip
    GET  /demo/trip/{session} the current trip picture
    POST /demo/reset          {session} -> the session and everything in it are gone
    GET  /health

Privacy (docs/ATLAS_RELEASE.md): raw documents are never logged and never
returned; they live only in the visitor's in-memory session, which ends on
reset or after the TTL. One document is at most 150 KB. Requests are rate
limited per visitor. Unknown formats are refused - never stored, never sent
to a model. The page may load nothing but itself and its fonts.

    uvicorn app.atlas_web.app:create_app --factory
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.atlas_web import intake, view
from app.atlas_web.sessions import DemoSession, SessionStore
from app.journey import ingest
from app.trip import atlas_demo

log = logging.getLogger("atlas_web")
PAGE = Path(__file__).with_name("static") / "index.html"
SAMPLES = {"flight": "F1", "hotel": "F2", "restaurant": "F3", "transfer": "F13", "flight_change": "F1c"}
LIMITS = {"session": (10, 60.0), "import": (30, 60.0), "read": (120, 60.0)}     # requests per window (s)
UNREADABLE = {"outcome": "unreadable", "message": "Atlas can't read this booking format yet.",
              "detail": "No trip data was changed."}
ENDED = {"message": "This demo session has ended. Start again with your bookings or the samples."}
CSP = ("default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


class RateLimiter:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock, self.hits, self.lock = clock, defaultdict(deque), threading.Lock()

    def allow(self, bucket: str, who: str) -> bool:
        limit, window = LIMITS[bucket]
        now = self.clock()
        with self.lock:
            q = self.hits[(bucket, who)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


def _visitor(request: Request) -> str:
    """The address Google's front end saw. It APPENDS that to X-Forwarded-For, so the
    last entry is the one a visitor cannot forge (the first one they can)."""
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "unknown")


def _page_template() -> str:
    """The page with the playback scenes and the sample e-mail bodies embedded (computed once)."""
    def blob(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")

    samples = []                                             # in story order: what a visitor may paste
    for key, doc in SAMPLES.items():
        raw = (atlas_demo.FIX / atlas_demo.DOCS[doc][1]).read_text(encoding="utf-8")
        samples.append({"key": key, "label": atlas_demo.DOC_LABELS[doc], "text": raw.split("\n\n", 1)[1]})
    html = PAGE.read_text(encoding="utf-8")
    return (html.replace("/*ATLAS-PLAYBACK*/{}", blob(atlas_demo.run()))
                .replace("/*ATLAS-SAMPLES*/[]", blob(samples)))


def create_app(*, store: SessionStore | None = None, clock: Callable[[], float] = time.monotonic) -> FastAPI:
    store = store or SessionStore(clock=clock)
    limiter = RateLimiter(clock)
    template = _page_template()
    app = FastAPI(title="Korsakov Atlas demo", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/demo"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("error path=%s type=%s", request.url.path, type(exc).__name__)    # never the request body
        return JSONResponse(status_code=500, content={"message": "Something went wrong. Nothing was saved."})

    def limited(request: Request, bucket: str) -> JSONResponse | None:
        if limiter.allow(bucket, _visitor(request)):
            return None
        return JSONResponse(status_code=429, content={"message": "Too many requests. Please wait a minute."})

    def trip(s: DemoSession) -> dict[str, Any]:
        return view.picture(s.db, s.trip_id, s.world)

    def ingest_one(s: DemoSession, raw: str, kind: str, source: str) -> dict[str, Any]:
        items = atlas_demo.items_of(s.db, s.trip_id)
        before = {k: (v.status, v.current_revision) for k, v in items.items()}
        report = ingest.import_document(s.db, s.trip_id, kind=kind, raw=raw, source_id=source,
                                        received_at=datetime.now(timezone.utc))
        s.db.commit()
        item = report.item
        return view.understood(item, report, before.get(item.identity) if item is not None else None)

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        nonce = secrets.token_urlsafe(16)
        return HTMLResponse(template.replace("{{NONCE}}", nonce),
                            headers={"Content-Security-Policy": CSP.format(nonce=nonce)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/demo/sample")
    def sample(request: Request) -> JSONResponse:
        if (refused := limited(request, "session")) is not None:
            return refused
        s = store.create()
        with s.lock:
            cards = [ingest_one(s, (atlas_demo.FIX / atlas_demo.DOCS[d][1]).read_text(encoding="utf-8"),
                                atlas_demo.DOCS[d][0], f"sample:{d}") for d in ("F1", "F2", "F3")]
            return JSONResponse({"session": s.token, "understood": cards, "trip": trip(s)})

    @app.post("/demo/import")
    async def import_(request: Request) -> JSONResponse:
        if (refused := limited(request, "import")) is not None:
            return refused
        body = await request.body()
        if len(body) > intake.MAX_DOCUMENT_BYTES + 4096:
            return JSONResponse(status_code=413, content={"message": "This document is too large for the demo "
                                                                     "(limit 150 KB)."})
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return JSONResponse(status_code=400, content={"message": "Send the document as JSON text."})
        token = payload.get("session")
        s = store.get(token) if token else None
        if token and s is None:
            return JSONResponse(status_code=404, content=ENDED)
        if payload.get("sample") in SAMPLES:
            doc = SAMPLES[payload["sample"]]
            raw, kind, source = (atlas_demo.FIX / atlas_demo.DOCS[doc][1]).read_text(encoding="utf-8"), \
                atlas_demo.DOCS[doc][0], f"sample:{doc}"
        else:
            try:
                raw = intake.prepare(str(payload.get("text") or ""))
            except intake.TooLarge:
                return JSONResponse(status_code=413, content={"message": "This document is too large for the demo "
                                                                         "(limit 150 KB)."})
            kind, source = intake.recognise(raw), "pasted"
            if kind is None:                       # not stored, not sent anywhere
                log.info("import outcome=unreadable")
                return JSONResponse({"session": s.token if s else None, "understood": UNREADABLE,
                                     "trip": trip(s) if s else None})
        s = s or store.create()
        with s.lock:
            card = ingest_one(s, raw, kind, source)
            log.info("import outcome=%s kind=%s", card["outcome"], kind)
            return JSONResponse({"session": s.token, "understood": card, "trip": trip(s)})

    @app.get("/demo/trip/{token}")
    def get_trip(token: str, request: Request) -> JSONResponse:
        if (refused := limited(request, "read")) is not None:
            return refused
        s = store.get(token)
        if s is None:
            return JSONResponse(status_code=404, content=ENDED)
        with s.lock:
            return JSONResponse({"session": s.token, "trip": trip(s)})

    @app.post("/demo/reset")
    async def reset(request: Request) -> JSONResponse:
        try:
            token = json.loads(await request.body() or b"{}").get("session")
        except ValueError:
            token = None
        store.drop(token)
        return JSONResponse({"ok": True})

    return app
