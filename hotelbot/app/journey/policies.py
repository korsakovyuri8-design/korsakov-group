"""Trip policies (data/trips/policies.yaml, D-080): versioned; every derived
result records the version it used. Nothing here is hard-coded elsewhere."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parents[2] / "data" / "trips" / "policies.yaml"


def load() -> dict[str, Any]:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))
