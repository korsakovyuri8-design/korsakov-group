"""Conservative deterministic place resolution for Atlas Montenegro beta.

A resolved place is Atlas-derived knowledge, not something the guest
necessarily stated. Only unique exact aliases resolve. No fuzzy guessing,
network calls, LLM calls or device-location fallbacks.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parents[2] / "data" / "trips" / "montenegro_places.yaml"
DATA = yaml.safe_load(PATH.read_text(encoding="utf-8"))
REGISTRY_VERSION: str = DATA["version"]


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


GLOBAL_PATH = PATH.parent / "global_places.yaml"
GLOBAL_DATA = yaml.safe_load(GLOBAL_PATH.read_text(encoding="utf-8"))

INDEX: dict[str, dict[str, dict[str, Any]]] = {}

for document in (DATA, GLOBAL_DATA):
    for key, row in sorted((document.get("places") or {}).items()):
        aliases = list(row.get("aliases") or [])
        if not row.get("require_country_qualifier"):
            aliases.insert(0, row["canonical_name"])
        for alias in aliases:
            INDEX.setdefault(_norm(alias), {})[key] = row


def resolve_place(text: str | None) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None

    hits = INDEX.get(_norm(text), {})

    # Multiple aliases of the SAME entity are fine.
    # Multiple different entities are ambiguous and never guessed.
    if len(hits) != 1:
        return None

    key, row = next(iter(hits.items()))
    coords = row.get("coordinates") or {}

    return {
        "status": "resolved",
        "entity_key": key,
        "canonical_name": row["canonical_name"],
        "kind": row.get("kind"),
        "address": row.get("address"),
        "locality": row.get("locality"),
        "municipality": row.get("municipality"),
        "country": row.get("country"),
        "timezone": row.get("timezone"),
        "coordinates": {
            "lat": coords.get("lat"),
            "lon": coords.get("lon"),
        },
        "resolver": (
            "atlas_montenegro_seed"
            if key in DATA["places"]
            else "atlas_global_seed"
        ),
        "registry_version": (
            REGISTRY_VERSION
            if key in DATA["places"]
            else str(GLOBAL_DATA["version"])
        ),
        **(
            {"coordinate_precision": row["coordinate_precision"]}
            if row.get("coordinate_precision") is not None
            else {}
        ),
        "match": "exact_alias",
    }
