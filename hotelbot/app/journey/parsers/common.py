"""Shared pieces of the deterministic document parsers."""

from __future__ import annotations

import html
import re
from datetime import datetime

# status EVENTS (D-081), first match per line: an explicit reinstatement, a cancellation, a confirmation
STATUS = [(re.compile(r"\b(?:booking|reservation)\s+has been\s+(?:reinstated|rebooked)\b", re.I), "reinstate"),
          (re.compile(r"\b(?:booking|reservation)\s+(?:has been|is|was)\s+cancell?ed\b", re.I), "cancel"),
          (re.compile(r"\b(?:booking|reservation)\s+(?:is|remains)\s+confirmed\b", re.I), "confirm")]
ISSUED = re.compile(r"^Issued:\s*(?P<d>\d{4}-\d{2}-\d{2})\s+(?P<t>\d{2}:\d{2})\s+(?P<o>[+-]\d{2}:\d{2})\s*$")


def unquote(line: str) -> str:
    """Drop one level of e-mail quoting ("> "), keep the rest as written."""
    if line.startswith(">"):
        line = line[1:]
        if line.startswith(" "):
            line = line[1:]
    return line


def flat(line: str) -> str:
    """A line as plain text: quoting, HTML tags and entities, extra spaces removed."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", unquote(line)))
    return re.sub(r"\s+([.,;:!?])(?=\s|$)", r"\1", re.sub(r"\s+", " ", text)).strip()


def body(raw: str) -> list[tuple[str, str]]:
    """(plain text, the raw line it came from) for every non-empty body line
    (after the outer e-mail headers). The raw line is the evidence."""
    lines, started = [], False
    for line in raw.splitlines():
        if not started:
            started = line.strip() == ""
            continue
        text = flat(line)
        if text:
            lines.append((text, line))
    return lines


def status_event(text: str) -> str | None:
    for pattern, event in STATUS:
        if pattern.search(text):
            return event
    return None


def issued(text: str) -> datetime | None:
    m = ISSUED.match(text)
    return datetime.fromisoformat(f"{m.group('d')}T{m.group('t')}{m.group('o')}") if m else None


def local(day: str, hhmm: str | None = None) -> str:
    """'18 Oct 2026' [+ '20:00'] -> '2026-10-18' / '2026-10-18T20:00'."""
    d = datetime.strptime(re.sub(r"\s+", " ", day.strip()), "%d %b %Y")
    return d.strftime("%Y-%m-%d") + (f"T{hhmm}" if hhmm else "")


def country_of(address: str) -> str:
    return address.rsplit(",", 1)[-1].strip()
