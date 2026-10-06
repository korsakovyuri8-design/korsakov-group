"""Field resolution policies, loaded from data/world/policies.yaml.

Authority is per field (no global source priority), freshness per fact
class, risk per field. Changing who wins for opening hours is a config
change, not a code change."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "world" / "policies.yaml"


@dataclass(frozen=True)
class FieldPolicy:
    name: str
    fact_class: str
    risk: str
    authority: tuple[str, ...]
    equal_within_m: float | None = None

    def rank(self, source_class: str) -> int:
        """0 = strongest. Unlisted classes rank after every listed one."""
        return self.authority.index(source_class) if source_class in self.authority else len(self.authority)

    @property
    def high_risk(self) -> bool:
        return self.risk == "high"


@dataclass(frozen=True)
class Policies:
    source_classes: tuple[str, ...]
    correction_classes: dict[str, str]
    freshness: dict[str, tuple[timedelta, timedelta]]
    fields: dict[str, FieldPolicy] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    same_rank_recency_gap: timedelta = timedelta(days=7)
    majority_wins: bool = True
    existence_grace: timedelta = timedelta(days=30)
    closed_authority: tuple[str, ...] = ()

    def field(self, name: str) -> FieldPolicy:
        if name in self.fields:
            return self.fields[name]
        prefix = name.split(".", 1)[0] + ".*"
        base = self.fields.get(prefix)
        if base is not None:
            return FieldPolicy(name, base.fact_class, base.risk, base.authority, base.equal_within_m)
        d = self.defaults
        return FieldPolicy(name, d.get("fact_class", "static"), d.get("risk", "low"), tuple(d.get("authority", ())))


def _sla(spec: dict[str, Any]) -> tuple[timedelta, timedelta]:
    if "fresh_minutes" in spec:
        return timedelta(minutes=spec["fresh_minutes"]), timedelta(minutes=spec["aging_minutes"])
    return timedelta(days=spec["fresh_days"]), timedelta(days=spec["aging_days"])


def parse(raw: dict[str, Any]) -> Policies:
    defaults = raw.get("defaults", {})
    fields = {}
    for name, spec in (raw.get("fields") or {}).items():
        spec = spec or {}
        fields[name] = FieldPolicy(name, spec.get("fact_class", defaults.get("fact_class", "static")),
                                   spec.get("risk", defaults.get("risk", "low")),
                                   tuple(spec.get("authority", defaults.get("authority", ()))),
                                   spec.get("equal_within_m"))
    res, ex = raw.get("resolution", {}), raw.get("existence", {})
    return Policies(
        source_classes=tuple(raw.get("source_classes", ())),
        correction_classes=dict(raw.get("correction_classes", {})),
        freshness={k: _sla(v) for k, v in (raw.get("freshness") or {}).items()},
        fields=fields, defaults=defaults,
        same_rank_recency_gap=timedelta(days=res.get("same_rank_recency_gap_days", 7)),
        majority_wins=bool(res.get("majority_wins", True)),
        existence_grace=timedelta(days=ex.get("grace_days", 30)),
        closed_authority=tuple(ex.get("closed_authority", ())),
    )


@lru_cache(maxsize=1)
def load(path: str | None = None) -> Policies:
    return parse(yaml.safe_load(Path(path or POLICY_PATH).read_text(encoding="utf-8")))
