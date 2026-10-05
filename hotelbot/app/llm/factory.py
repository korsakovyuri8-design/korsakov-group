from __future__ import annotations

from app.config import Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.openai_provider import OpenAICompatibleProvider


def build_llm(settings: Settings) -> LLMProvider | None:
    """Provider selected by HOTELBOT_LLM_PROVIDER; None means run without an LLM."""
    if settings.llm_provider == "none":
        return None
    common = dict(
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
    )
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("HOTELBOT_OPENAI_API_KEY is required for the openai provider")
        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key.get_secret_value(), base_url=settings.openai_base_url, **common
        )
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("HOTELBOT_ANTHROPIC_API_KEY is required for the anthropic provider")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(), base_url=settings.anthropic_base_url, **common
        )
    raise ValueError(f"unknown LLM provider {settings.llm_provider!r}")
