"""Hotel booking confirmations (F2), plain text or HTML (F9). Check-in and
check-out are DATES; the hotel's 'from 14:00' / 'until 11:00' are its
stated policy, kept as details - a date never becomes a time."""

from __future__ import annotations

import re

from app.journey.parsers import common
from app.journey.parsers.airline import Parsed

_HEADER = re.compile(r"^(?P<p>.+?)\s+-\s+(?P<t>booking confirmation|booking cancellation)$", re.I)
_REF = re.compile(r"^Confirmation number:\s*(?P<v>[A-Z0-9-]{4,16})$")
_DATE = r"(?P<d>\d{1,2}\s+\w{3}\s+\d{4})"
_IN = re.compile(rf"^Check-in:\s*{_DATE}(?:\s*\(from\s+(?P<h>\d{{2}}:\d{{2}})\))?$")
_OUT = re.compile(rf"^Check-out:\s*{_DATE}(?:\s*\(until\s+(?P<h>\d{{2}}:\d{{2}})\))?$")


def parse(raw: str) -> Parsed | None:
    out = Parsed("accommodation")
    country = None
    pending: list[tuple[str, dict, str]] = []
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
        elif text.startswith("Guest: "):
            out.observations.append(("guest_name", text[len("Guest: "):], line))
        elif text.startswith("Address: "):
            address = text[len("Address: "):]
            country = common.country_of(address)
            out.observations.append(("address", address, line))
        elif m := _IN.match(text):
            pending.append(("check_in", {"raw": m.group("d"), "local": common.local(m.group("d")), "date_only": True},
                            line))
            if m.group("h"):
                out.observations.append(("check_in_from", m.group("h"), line))
        elif m := _OUT.match(text):
            pending.append(("check_out", {"raw": m.group("d"), "local": common.local(m.group("d")),
                                          "date_only": True}, line))
            if m.group("h"):
                out.observations.append(("check_out_until", m.group("h"), line))
        elif text.startswith("Room: "):
            out.observations.append(("room", text[len("Room: "):], line))
        elif m := re.match(r"^Guests:\s*(\d{1,2})$", text):
            out.observations.append(("guests", int(m.group(1)), line))
        if event := common.status_event(text):
            out.observations.append(("status_event", event, line))
    for name, value, line in pending:                  # the address names the country = the zone
        out.observations.append((name, {**value, "place": country, "place_kind": "country"}, line))
    fields = {f for f, _, _ in out.observations}
    if not (out.provider and out.booking_reference and {"check_in", "check_out"} <= fields):
        return None
    return out
