"""OpenAI-compatible Chat Completions provider.

Works with OpenAI and any server exposing the same `/chat/completions` API
(Azure-style gateways, vLLM, Ollama, LM Studio, OpenRouter...) via
HOTELBOT_OPENAI_BASE_URL.
"""

from __future__ import annotations

import httpx

from app.llm.base import ChatMessage, LLMError


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float, max_tokens: int,
                 client: httpx.Client | None = None) -> None:
        if not model:
            raise ValueError("HOTELBOT_LLM_MODEL must be set for the openai provider")
        self.model = model
        self.max_tokens = max_tokens
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=timeout)

    def complete(self, system: str, messages: list[ChatMessage], *, max_tokens: int | None = None,
                 temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
        }
        try:
            resp = self._client.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"openai transport error: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise LLMError(f"openai HTTP {resp.status_code}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("openai malformed response") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("openai empty response")
        return content
