"""Escalation policy: *when* a conversation goes to a human, and how urgently.

Kept as pure functions over the intent result and conversation counters so
the rules are easy to read, test and tune (ChatGPT owns the conversation
policy; this module is the single place to encode it).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.agent.intents import Intent, IntentResult
from app.db.models import RequestType, Urgency
from app.text import fold


class HandoffReason(str, enum.Enum):
    EMERGENCY = "emergency"
    HUMAN_REQUESTED = "human_requested"
    COMPLAINT = "complaint"
    BILLING = "billing"
    BOOKING_MODIFICATION = "booking_modification"
    REPEATED_FAILURE = "repeated_failure"


@dataclass(frozen=True)
class HandoffDecision:
    reason: HandoffReason
    urgency: Urgency
    reply_key: str  # message catalog key for the guest-facing reply


def handoff_for_intent(result: IntentResult) -> HandoffDecision | None:
    """Handoffs that follow directly from what the guest said."""
    if result.intent == Intent.EMERGENCY:
        return HandoffDecision(HandoffReason.EMERGENCY, Urgency.CRITICAL, "emergency")
    if result.intent == Intent.HUMAN_REQUEST:
        return HandoffDecision(HandoffReason.HUMAN_REQUESTED, Urgency.NORMAL, "handoff_human")
    if "billing" in result.flags:
        return HandoffDecision(HandoffReason.BILLING, Urgency.HIGH, "handoff_billing")
    if result.intent == Intent.COMPLAINT:
        return HandoffDecision(HandoffReason.COMPLAINT, Urgency.HIGH, "handoff_complaint")
    if result.intent == Intent.BOOKING_REQUEST and "booking_change" in result.flags:
        return HandoffDecision(HandoffReason.BOOKING_MODIFICATION, Urgency.NORMAL, "booking_change")
    return None


@dataclass(frozen=True)
class FailurePolicy:
    """How many consecutive unhelpful turns are tolerated before a human
    takes over. `per_intent` refines the default for specific intents (the
    intent of the turn that failed), e.g. {"LOCAL_RECOMMENDATION": 1}.

    Resolution order: property pack policy > deployment settings > default 2.
    """

    default: int = 2
    per_intent: Mapping[str, int] = field(default_factory=dict)

    def threshold(self, intent: Intent | str | None) -> int:
        key = intent.value if isinstance(intent, Intent) else intent
        return self.per_intent.get(key, self.default) if key else self.default

    @classmethod
    def resolve(cls, default: int, overrides: Mapping[str, int] | None,
                property_policy: Mapping[str, Any] | None) -> FailurePolicy:
        pp = property_policy or {}
        per_intent = {**(overrides or {}), **(pp.get("failure_thresholds") or {})}
        return cls(default=pp.get("max_consecutive_failures") or default, per_intent=per_intent)


def handoff_for_failures(consecutive_failures: int, max_failures: int | FailurePolicy,
                         intent: Intent | str | None = None) -> HandoffDecision | None:
    """Escalate after the bot repeatedly could not understand or ground an answer."""
    threshold = max_failures.threshold(intent) if isinstance(max_failures, FailurePolicy) else max_failures
    if consecutive_failures >= threshold:
        return HandoffDecision(HandoffReason.REPEATED_FAILURE, Urgency.NORMAL, "handoff_failure")
    return None


def request_urgency(request_type: RequestType) -> Urgency:
    return Urgency.HIGH if request_type == RequestType.MAINTENANCE else Urgency.NORMAL


_AFFIRMATIVE_RAW = {
    "yes", "yeah", "yep", "sure", "ok", "okay", "please", "yes please", "please do", "go ahead",
    "yes, please", "do it", "book it", "send it", "yes book it", "yes, book it", "please book it",
    "da", "moze", "naravno", "svakako", "vazi", "u redu", "da molim", "da, molim", "molim vas", "moze, hvala",
    "da hvala", "da, hvala", "rezervisite", "posaljite", "uradite to",
    "да", "да, пожалуйста", "да пожалуйста", "конечно", "давайте", "хорошо", "ок", "бронируйте", "отправьте",
}
_NEGATIVE_RAW = {
    "no", "nope", "no thanks", "no, thanks", "not now",
    "ne", "ne hvala", "ne, hvala", "nema potrebe",
    "нет", "нет, спасибо", "нет спасибо", "не надо",
}
# Compared against folded guest text (Cyrillic transliterated, no diacritics).
AFFIRMATIVE = frozenset(fold(x) for x in _AFFIRMATIVE_RAW)
NEGATIVE = frozenset(fold(x) for x in _NEGATIVE_RAW)
