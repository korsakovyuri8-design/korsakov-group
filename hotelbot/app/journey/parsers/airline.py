"""Airline confirmation / schedule-change e-mails (fixture family F1).

Reads the provider's own lines - also when the e-mail was forwarded ("> "
quoting) or re-sent with other spacing. Every observation carries the raw
line(s) it came from. Status only from an explicit provider sentence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_PROVIDER = re.compile(r"^(?P<p>[A-Z][\w .&'-]+?)\s+-\s+(?P<t>booking confirmation|schedule change|cancellation)\s*$",
                       re.I)
_DOC_TYPE = {"booking confirmation": "confirmation", "schedule change": "change", "cancellation": "cancellation"}
_REF = re.compile(r"^Booking reference:\s*(?P<v>[A-Z0-9]{5,8})\s*$")
_ISSUED = re.compile(r"^Issued:\s*(?P<d>\d{4}-\d{2}-\d{2})\s+(?P<t>\d{2}:\d{2})\s+(?P<o>[+-]\d{2}:\d{2})\s*$")
_FLIGHT = re.compile(r"^Flight\s+(?P<c>[A-Z0-9]{2})\s*(?P<n>\d{1,4})\s*$")
_LEG = re.compile(r"^(?P<w>Depart|Arrive):\s*(?P<name>.+?)\s*\((?P<code>[A-Z]{3})\)\s+(?P<d>\d{1,2}\s+\w{3}\s+\d{4})"
                  r"\s+(?P<t>\d{2}:\d{2})\s*$")
_ORIGINAL = re.compile(r"^Originally scheduled:\s*(?P<d>\d{1,2}\s+\w{3}\s+\d{4})\s+(?P<t>\d{2}:\d{2})\s*$")
_CABIN = re.compile(r"^Cabin:\s*(?P<v>\w[\w ]*?)\s*$")
# status EVENTS (D-081), first match per line: an explicit reinstatement, a cancellation, a confirmation
_STATUS = [(re.compile(r"\b(?:booking|reservation)\s+has been\s+(?:reinstated|rebooked)\b", re.I), "reinstate"),
           (re.compile(r"\b(?:booking|reservation)\s+(?:has been|is|was)\s+cancell?ed\b", re.I), "cancel"),
           (re.compile(r"\b(?:booking|reservation)\s+(?:is|remains)\s+confirmed\b", re.I), "confirm")]


@dataclass
class Parsed:
    kind: str
    doc_type: str | None = None        # confirmation | change | cancellation
    provider: str | None = None
    booking_reference: str | None = None
    issued_at: datetime | None = None
    observations: list[tuple[str, Any, str]] = field(default_factory=list)   # (field, value, evidence)


def _unquote(line: str) -> str:
    """Drop one level of e-mail quoting ("> "), keep the rest as written."""
    if line.startswith(">"):
        line = line[1:]
        if line.startswith(" "):
            line = line[1:]
    return line


def parse(raw: str) -> Parsed | None:
    lines = raw.splitlines()
    out = Parsed("flight")
    body = False
    passengers: list[tuple[str, str]] = []
    in_passengers: str | None = None
    for line in lines:
        if not body:                      # skip the outer e-mail headers
            body = line.strip() == ""
            continue
        text = _unquote(line)
        flat = re.sub(r"\s+", " ", text).strip()
        if in_passengers is not None:
            if text[:1] in (" ", "\t") and flat:
                passengers.append((flat, line))
                continue
            in_passengers = None
        if m := _PROVIDER.match(flat):
            if out.provider is None:
                out.provider = m.group("p").strip().lower()
                out.doc_type = _DOC_TYPE[m.group("t").lower()]
                out.observations.append(("provider", out.provider, line))
        elif m := _REF.match(flat):
            out.booking_reference = m.group("v")
            out.observations.append(("booking_reference", m.group("v"), line))
        elif m := _ISSUED.match(flat):
            out.issued_at = datetime.fromisoformat(f"{m.group('d')}T{m.group('t')}{m.group('o')}")
            out.observations.append(("issued_at", out.issued_at.isoformat(), line))
        elif m := _FLIGHT.match(flat):
            out.observations += [("carrier", m.group("c"), line), ("flight_number", m.group("c") + m.group("n"), line)]
        elif m := _LEG.match(flat):
            name = "departs" if m.group("w") == "Depart" else "arrives"
            out.observations.append(("origin" if name == "departs" else "destination", m.group("code"), line))
            raw_value = f"{re.sub(chr(32) + '+', ' ', m.group('d'))} {m.group('t')}"
            out.observations.append((name, {"raw": raw_value, "place": m.group("code"), "place_kind": "airport"}, line))
        elif m := _ORIGINAL.match(flat):
            raw_value = f"{re.sub(chr(32) + '+', ' ', m.group('d'))} {m.group('t')}"
            out.observations.append(("original_departure", {"raw": raw_value}, line))
        elif m := _CABIN.match(flat):
            out.observations.append(("cabin", m.group("v"), line))
        elif flat == "Passengers:":
            in_passengers = line
        for pattern, event in _STATUS:
            if pattern.search(flat):
                out.observations.append(("status_event", event, line))
                break
    if passengers:
        start = lines.index(next(raw_line for raw_line in lines if _unquote(raw_line).strip() == "Passengers:"))
        block = "\n".join(lines[start:start + 1 + len(passengers)])
        out.observations.append(("passengers", [p for p, _ in passengers], block))
    fields = {f for f, _, _ in out.observations}
    if not (out.provider and out.booking_reference and {"flight_number", "departs", "arrives"} <= fields):
        return None
    return out
