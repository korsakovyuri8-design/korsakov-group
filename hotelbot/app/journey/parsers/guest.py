"""A guest's own statement in chat (F6). Deterministic phrases only; it is
STATED, never a booking, has no provider and no booking reference."""

from __future__ import annotations

import json
import re
from datetime import datetime

from app.journey.parsers import common
from app.journey.parsers.airline import Parsed

_ARRIVAL = re.compile(r"\bland(?:s|ing)?\s+in\s+(?P<city>[A-Z][\w-]+(?:\s[A-Z][\w-]+)?)\s+on\s+"
                      r"(?P<d>\d{1,2}\s\w{3}\s\d{4})\s+around\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?\b")


def parse(raw: str) -> Parsed | None:
    msg = json.loads(raw)
    text = msg.get("text") or ""
    m = _ARRIVAL.search(text)
    if not m:
        return None
    hour = int(m.group("h")) % 12 + (12 if m.group("ap") == "pm" else 0) if m.group("ap") else int(m.group("h"))
    hhmm = f"{hour:02d}:{m.group('m') or '00'}"
    out = Parsed("statement", doc_type="statement",
                 issued_at=datetime.fromisoformat(msg["sent_at"]) if msg.get("sent_at") else None)
    phrase = m.group(0)
    out.observations += [
        ("subject", "arrival", phrase),
        ("city", m.group("city"), m.group("city")),
        ("at", {"raw": phrase, "local": common.local(m.group("d"), hhmm), "approximate": True,
                "place": m.group("city"), "place_kind": "city"}, phrase),
        ("status_event", "state", text),
    ]
    return out
