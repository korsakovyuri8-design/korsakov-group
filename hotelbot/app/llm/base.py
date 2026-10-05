"""Provider-neutral LLM interface.

The agent only ever calls `LLMProvider.complete(system, messages, ...)` and
gets text back. Providers translate to their wire format, apply timeouts and
raise `LLMError` for anything unexpected (HTTP errors, timeouts, malformed
payloads) so callers have exactly one failure type to handle.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, TypedDict


class ChatMessage(TypedDict):
    role: str      # "user" | "assistant"
    content: str


class LLMError(Exception):
    """Any provider failure: transport, HTTP status, or malformed response."""


class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        system: str,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str: ...


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse a JSON object from model output, tolerating code fences and
    leading/trailing prose. Raises ValueError if no object can be parsed."""
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text)] + [text]
    for candidate in candidates:
        candidate = candidate.strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object in model output")
