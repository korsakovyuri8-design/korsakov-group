"""Restaurant reservations from a booking platform (F3 / F3b / F3c)."""

from __future__ import annotations

import re

from app.journey.parsers import common
from app.journey.parsers.airline import Parsed

_HEADER = re.compile(r"^(?P<p>.+?)\s+-\s+(?P<t>reservation confirmation|reservation cancellation)$", re.I)
_REF = re.compile(r"^Reservation code:\s*(?P<v>[A-Z0-9-]{4,16})$")
_DAY = re.compile(r"^Date:\s*(?P<d>\d{1,2}\s+\w{3}\s+\d{4})$")
_TIME = re.compile(r"^Time:\s*(?P<t>\d{2}:\d{2})$")


def parse(raw: str) -> Parsed | None:
    out = Parsed("restaurant")
    country = day = hhmm = None
    day_line = time_line = None
    for text, line in common.body(raw):
        if (m := _HEADER.match(text)) and out.provider is None:
            out.provider = m.group("p").strip().lower()
            out.doc_type = "cancellation" if "cancellation" in m.group("t").lower() else "confirmation"
            out.observations.append(("provider", out.provider, line))
        elif m := _REF.match(text):
            out.booking_reference = m.group("v")
            out.observations.append(("booking_reference", m.group("v"), line))
        elif (when := common.issued(text)) is not None:
            out.issued_at = when
            out.observations.append(("issued_at", when.isoformat(), line))
        elif text.startswith("Restaurant: "):
            out.observations.append(("venue", text[len("Restaurant: "):], line))
        elif text.startswith("Address: "):
            address = text[len("Address: "):]
            country = common.country_of(address)
            out.observations.append(("address", address, line))
        elif m := _DAY.match(text):
            day, day_line = m.group("d"), line
        elif m := _TIME.match(text):
            hhmm, time_line = m.group("t"), line
        elif m := re.match(r"^Party:\s*(\d{1,2})$", text):
            out.observations.append(("party", int(m.group(1)), line))
        if event := common.status_event(text):
            out.observations.append(("status_event", event, line))
    if day and hhmm:
        lines = raw.splitlines()
        a, b = sorted((lines.index(day_line), lines.index(time_line)))
        evidence = "\n".join(lines[a:b + 1])                # the provider's own Date / Time lines
        out.observations.append(("starts", {"raw": f"{day} {hhmm}", "local": common.local(day, hhmm),
                                            "place": country, "place_kind": "country"}, evidence))
    fields = {f for f, _, _ in out.observations}
    if not (out.provider and out.booking_reference and "starts" in fields):
        return None
    return out
