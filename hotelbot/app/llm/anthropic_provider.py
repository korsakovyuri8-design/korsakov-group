"""Anthropic Messages API provider."""

from __future__ import annotations

import httpx

from app.llm.base import ChatMessage, LLMError

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float, max_tokens: int,
                 client: httpx.Client | None = None) -> None:
        if not model:
            raise ValueError("HOTELBOT_LLM_MODEL must be set for the anthropic provider")
        self.model = model
        self.max_tokens = max_tokens
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
        self._client = client or httpx.Client(timeout=timeout)

    def complete(self, system: str, messages: list[ChatMessage], *, max_tokens: int | None = None,
                 temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "system": system,
            "messages": _merge_consecutive(messages),
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
        }
        try:
            resp = self._client.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic transport error: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise LLMError(f"anthropic HTTP {resp.status_code}")
        try:
            blocks = resp.json()["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LLMError("anthropic malformed response") from exc
        if not text.strip():
            raise LLMError("anthropic empty response")
        return text


def _merge_consecutive(messages: list[ChatMessage]) -> list[ChatMessage]:
    """The Messages API requires alternating roles starting with "user"."""
    merged: list[ChatMessage] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n\n" + m["content"]}
        else:
            merged.append({"role": m["role"], "content": m["content"]})
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(conversation start)"})
    return merged
