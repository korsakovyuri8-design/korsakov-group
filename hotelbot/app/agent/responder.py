"""Grounded answer generation.

`GroundedResponder` turns a retrieval result into a guest reply:

* With an LLM: the model phrases an answer from the retrieved items only and
  must return `{"answerable", "answer", "sources"}`. The answer is accepted
  only if it is answerable AND cites at least one retrieved key (unknown
  keys are rejected). An explicit "not answerable" or an uncited answer is
  treated as "not grounded". Provider errors or unparseable output fall back
  to the extractive answer, since the retrieved knowledge itself is valid.
* Without an LLM: the reply is the retrieved text itself (extractive), so it
  cannot contain anything that is not in the knowledge pack.

Response authority: LLM prose that claims a real-world state change
("your late checkout is confirmed") without stored action state backing it
is discarded in favour of the property-authored text (app/agent/authority.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.agent import messages as msg
from app.agent.authority import unbacked_claims
from app.agent.prompts import GROUNDED_ANSWER_SYSTEM, LANGUAGE_NAMES, format_knowledge
from app.db.models import ActionStatus
from app.knowledge.schemas import RetrievalResult
from app.llm.base import ChatMessage, LLMError, LLMProvider, extract_json
from app.observability import log_event


@dataclass
class GroundedAnswer:
    grounded: bool
    text: str | None = None
    sources: list[str] = field(default_factory=list)
    mode: str = "extractive"


class _LLMAnswer(BaseModel):
    answerable: bool
    answer: str = ""
    sources: list[str] = []


class GroundedResponder:
    MAX_EXTRACTIVE_ITEMS = 2

    def __init__(self, llm: LLMProvider | None, property_name: str = "", property_type: str = "hotel") -> None:
        self.llm = llm
        self.default_property_name = property_name
        self.default_property_type = property_type

    def answer(self, retrieval: RetrievalResult, language: str,
               history: list[ChatMessage] | None = None, *, property_name: str | None = None,
               property_type: str | None = None,
               action_statuses: list[ActionStatus] | None = None) -> GroundedAnswer:
        items = retrieval.grounded_items
        if not items:
            return GroundedAnswer(grounded=False)
        if self.llm is None:
            return self._extractive(retrieval, language)
        return self._generate(retrieval, language, history or [],
                              property_name or self.default_property_name,
                              property_type or self.default_property_type,
                              action_statuses or [])

    def _extractive(self, retrieval: RetrievalResult, language: str) -> GroundedAnswer:
        items = retrieval.grounded_items
        top = items[0].score
        chosen = [i for i in items if i.score >= top * 0.9][: self.MAX_EXTRACTIVE_ITEMS]
        text = " ".join(i.text_for(language) for i in chosen)
        if not all(i.has_locale(language) for i in chosen):
            # Say so rather than silently switching language.
            text = msg.t("knowledge_language_fallback", language, text=text)
        return GroundedAnswer(grounded=True, text=text, sources=[i.key for i in chosen], mode="extractive")

    def _generate(self, retrieval: RetrievalResult, language: str, history: list[ChatMessage],
                  property_name: str, property_type: str, action_statuses: list[ActionStatus]) -> GroundedAnswer:
        items = retrieval.grounded_items
        system = GROUNDED_ANSWER_SYSTEM.format(
            property=property_name,
            property_type=property_type.replace("_", " "),
            language=LANGUAGE_NAMES.get(language, "English"),
            knowledge=format_knowledge(items, language),
        )
        messages = [*history, ChatMessage(role="user", content=retrieval.query)]
        try:
            raw = self.llm.complete(system=system, messages=messages, temperature=0.1)  # type: ignore[union-attr]
            parsed = _LLMAnswer.model_validate(extract_json(raw))
        except (LLMError, ValidationError, ValueError) as exc:
            # Provider down or unusable output: the retrieved knowledge is still
            # valid, so degrade to quoting it rather than failing the guest.
            log_event("grounded_answer_failed", reason=type(exc).__name__, fallback="extractive")
            return self._extractive(retrieval, language)
        allowed = {i.key for i in items}
        cited = [s for s in parsed.sources if s in allowed]
        if not parsed.answerable or not parsed.answer.strip() or not cited or len(cited) != len(parsed.sources):
            log_event("grounded_answer_rejected", answerable=parsed.answerable, sources=parsed.sources)
            return GroundedAnswer(grounded=False, mode="llm")
        claims = unbacked_claims(parsed.answer, action_statuses)
        if claims:
            log_event("authority_violation_blocked", claims=sorted(c.value for c in claims), fallback="extractive")
            return self._extractive(retrieval, language)
        return GroundedAnswer(grounded=True, text=parsed.answer.strip(), sources=cited, mode="llm")
