"""YAML loading with libyaml's C loader when available (data packs are large)."""

from __future__ import annotations

from typing import Any

import yaml

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def yaml_load(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)  # noqa: S506 - SafeLoader variants only
