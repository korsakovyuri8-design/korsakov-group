"""Escalation policy: *when* a conversation goes to a human, and how urgently.

Kept as pure functions over the intent result and conversation counters so
the rules are easy to read, test and tune (ChatGPT owns the conversation
policy; this module is the single place to encode it).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.agent.intents import Intent, IntentResult
from app.db.models import RequestType, Urgency


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


def handoff_for_failures(consecutive_failures: int, max_failures: int) -> HandoffDecision | None:
    """Escalate after the bot repeatedly could not understand or ground an answer."""
    if consecutive_failures >= max_failures:
        return HandoffDecision(HandoffReason.REPEATED_FAILURE, Urgency.NORMAL, "handoff_failure")
    return None


def request_urgency(request_type: RequestType) -> Urgency:
    return Urgency.HIGH if request_type == RequestType.MAINTENANCE else Urgency.NORMAL


AFFIRMATIVE = {
    "yes", "yeah", "yep", "sure", "ok", "okay", "please", "yes please", "please do", "go ahead",
    "da", "moze", "naravno", "svakako", "vazi", "u redu", "da molim", "da, molim", "molim vas", "moze, hvala",
    "yes, please", "da hvala", "da, hvala",
}
NEGATIVE = {"no", "nope", "no thanks", "no, thanks", "ne", "ne hvala", "ne, hvala", "nema potrebe", "not now"}
