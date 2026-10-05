"""Knowledge pack format.

A knowledge pack is a YAML (or JSON) file that holds everything the bot is
allowed to state as fact about one hotel. It is pure data: swapping the
synthetic pack for the real Hotel Aleksandar pack requires no code change.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

SUPPORTED_LANGUAGES = ("en", "cnr")


class KnowledgeItem(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_\-]+$", max_length=128)
    category: str
    # Per-language text. At least one language must be present.
    content: dict[str, str]
    # Extra retrieval terms per language (synonyms, colloquial words).
    keywords: dict[str, list[str]] = Field(default_factory=dict)
    source: str | None = None  # overrides the pack-level source
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def _non_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not any(text.strip() for text in v.values()):
            raise ValueError("knowledge item needs content in at least one language")
        return v


class PackInfo(BaseModel):
    hotel_slug: str
    hotel_name: str
    source: str
    synthetic: bool = False
    version: str | None = None


class KnowledgePack(BaseModel):
    pack: PackInfo
    items: list[KnowledgeItem]

    @field_validator("items")
    @classmethod
    def _unique_keys(cls, v: list[KnowledgeItem]) -> list[KnowledgeItem]:
        keys = [i.key for i in v]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate knowledge item keys: {sorted(dupes)}")
        return v


class RetrievedItem(BaseModel):
    """A knowledge item returned by a retriever, with its relevance score."""

    key: str
    category: str
    source: str
    content: dict[str, str]
    metadata: dict[str, Any]
    score: float

    def text_for(self, language: str) -> str:
        """Content in the requested language, falling back to English, then anything."""
        return self.content.get(language) or self.content.get("en") or next(iter(self.content.values()))


class RetrievalResult(BaseModel):
    query: str
    items: list[RetrievedItem]
    min_score: float

    @property
    def grounded(self) -> bool:
        return bool(self.items) and self.items[0].score >= self.min_score

    @property
    def grounded_items(self) -> list[RetrievedItem]:
        return [i for i in self.items if i.score >= self.min_score]
