"""Ground transfer confirmations (F13). A pickup / drop-off is an airport
("... (TGD)") or a street address; its zone comes from the airport code or
from the country the address names - never guessed."""

from __future__ import annotations

import re

from app.journey.parsers import common
from app.journey.parsers.airline import Parsed

_HEADER = re.compile(r"^(?P<p>.+?)\s+-\s+(?P<t>transfer confirmation|transfer cancellation)$", re.I)
_REF = re.compile(r"^Booking reference:\s*(?P<v>[A-Z0-9-]{4,16})$")
_LEG = re.compile(r"^(?P<leg>Pickup|Drop-off):\s*(?P<place>.+?)\s+(?P<d>\d{1,2}\s+\w{3}\s+\d{4})\s+(?P<t>\d{2}:\d{2})$")
_AIRPORT = re.compile(r"\((?P<code>[A-Z]{3})\)$")


def _place(text: str) -> tuple[str, str, str | None]:
    """(place, place kind, registry key) for a pickup / drop-off."""
    if m := _AIRPORT.search(text):
        return m.group("code"), "airport", m.group("code")
    return text, "address", common.country_of(text)


def parse(raw: str) -> Parsed | None:
    out = Parsed("transfer")
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
        elif m := _LEG.match(text):
            name = "pickup" if m.group("leg") == "Pickup" else "dropoff"
            place, kind, key = _place(m.group("place"))
            out.observations += [
                (name, place, line), (f"{name}_place_kind", kind, line),
                (f"{name}_at", {"raw": f"{m.group('d')} {m.group('t')}", "local": common.local(m.group("d"), m.group("t")),
                                "place": key, "place_kind": "airport" if kind == "airport" else "country"}, line)]
        elif m := re.match(r"^Passengers:\s*(\d{1,2})$", text):
            out.observations.append(("passengers", int(m.group(1)), line))
        if event := common.status_event(text):
            out.observations.append(("status_event", event, line))
    fields = {f for f, _, _ in out.observations}
    if not (out.provider and out.booking_reference and {"pickup_at", "dropoff_at"} <= fields):
        return None
    return out
