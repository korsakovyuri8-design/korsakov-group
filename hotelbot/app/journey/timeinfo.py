"""Time records: raw -> local wall time -> IANA zone -> UTC -> evidence.

A zone comes only from the versioned registry (data/trips/tz_registry.yaml,
D-079); a place not in it has an UNKNOWN zone and no UTC - never guessed,
never the device's zone. A local time that DST makes ambiguous (fall back)
or non-existent (spring forward) gets no UTC either: it is an issue, not a
silent fold=0/1 choice."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "trips" / "tz_registry.yaml"
_REGISTRY = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
REGISTRY_VERSION: str = _REGISTRY["version"]
_KINDS = {"airport": "airports", "station": "stations", "port": "ports"}


def zone_for(code: str | None, kind: str) -> str | None:
    if code is None:
        return None
    return (_REGISTRY.get(_KINDS.get(kind, ""), {}) or {}).get(code)


def time_record(raw: str, local: datetime, code: str | None, kind: str, *, evidence: str | None) -> tuple[dict[str, Any], str | None]:
    """(record, issue). `local` is the naive wall time at the place."""
    tz = zone_for(code, kind)
    rec: dict[str, Any] = {"raw": raw, "local": local.strftime("%Y-%m-%dT%H:%M"), "tz": tz, "utc": None,
                           "place": code, "evidence": evidence, "registry": REGISTRY_VERSION}
    if tz is None:
        return rec, "unknown_timezone"
    z = ZoneInfo(tz)
    first, second = local.replace(tzinfo=z, fold=0), local.replace(tzinfo=z, fold=1)
    if first.utcoffset() != second.utcoffset():
        exists = first.astimezone(timezone.utc).astimezone(z).replace(tzinfo=None) == local
        return rec, "ambiguous_local_time" if exists else "nonexistent_local_time"
    rec["utc"] = first.astimezone(timezone.utc).isoformat()
    return rec, None


def record_from_value(value: dict[str, Any], *, evidence: str | None) -> tuple[dict[str, Any], str | None]:
    """A parser's time value -> time record. `local` is 'YYYY-MM-DDTHH:MM'
    (or just 'YYYY-MM-DD' with date_only: a date never becomes 00:00)."""
    local = value["local"]
    if value.get("date_only") or len(local) == 10:
        tz = zone_for(value["place"], value["place_kind"]) if value.get("place") else None
        rec = {"raw": value["raw"], "local": local[:10], "date_only": True, "tz": tz, "utc": None,
               "place": value.get("place"), "evidence": evidence, "registry": REGISTRY_VERSION}
        return rec, None if tz else "unknown_timezone"
    rec, issue = time_record(value["raw"], datetime.fromisoformat(local), value.get("place"),
                             value.get("place_kind", ""), evidence=evidence)
    if value.get("approximate"):
        rec["approximate"] = True
    return rec, issue
