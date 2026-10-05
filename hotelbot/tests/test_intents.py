import pytest

from app.agent.intents import HybridIntentClassifier, Intent, RuleIntentClassifier, mentions_late_time
from app.db.models import RequestType
from app.llm.base import LLMError
from app.text import fold
from tests.conftest import ScriptedLLM

rules = RuleIntentClassifier()


@pytest.mark.parametrize(
    "text,intent,request_type",
    [
        ("What time is breakfast?", Intent.HOTEL_INFORMATION, None),
        ("Kada je doručak?", Intent.HOTEL_INFORMATION, None),
        ("Can we check in after 11pm?", Intent.SERVICE_REQUEST, RequestType.LATE_CHECK_IN),
        ("Možemo li se prijaviti poslije 23h?", Intent.SERVICE_REQUEST, RequestType.LATE_CHECK_IN),
        ("We will arrive late, around midnight, can you keep the room?", Intent.SERVICE_REQUEST, RequestType.LATE_CHECK_IN),
        ("Is late check-in possible?", Intent.HOTEL_INFORMATION, RequestType.LATE_CHECK_IN),
        ("Could you send more towels please", Intent.SERVICE_REQUEST, RequestType.HOUSEKEEPING),
        ("Treba mi još peškira u sobi 204", Intent.SERVICE_REQUEST, RequestType.HOUSEKEEPING),
        ("The shower is broken", Intent.SERVICE_REQUEST, RequestType.MAINTENANCE),
        ("Klima ne radi", Intent.SERVICE_REQUEST, RequestType.MAINTENANCE),
        ("Can you book us a taxi to Podgorica tomorrow?", Intent.SERVICE_REQUEST, RequestType.TRANSPORT),
        ("Do you have rooms available for 20 December?", Intent.BOOKING_REQUEST, RequestType.BOOKING),
        ("Želim da otkažem rezervaciju", Intent.BOOKING_REQUEST, RequestType.BOOKING),
        ("The room is dirty and the staff was rude", Intent.COMPLAINT, None),
        ("Soba je prljava, nezadovoljni smo", Intent.COMPLAINT, None),
        ("Fire! There is smoke in the room", Intent.EMERGENCY, None),
        ("Treba nam hitna pomoć!", Intent.EMERGENCY, None),
        ("I want to speak to someone at reception", Intent.HUMAN_REQUEST, None),
        ("Želim da razgovaram sa nekim, pravu osobu molim", Intent.HUMAN_REQUEST, None),
        ("What do you recommend to do nearby?", Intent.LOCAL_RECOMMENDATION, None),
        ("Šta preporučujete da posjetimo?", Intent.LOCAL_RECOMMENDATION, None),
        ("Hello!", Intent.GENERAL_CONVERSATION, None),
        ("Hvala puno", Intent.GENERAL_CONVERSATION, None),
        ("asdfgh", Intent.UNKNOWN, None),
    ],
)
def test_rule_classifier(text, intent, request_type):
    result = rules.classify(text)
    assert result.intent == intent, result.signals
    assert result.request_type == request_type


def test_billing_dispute_is_complaint_with_billing_flag():
    result = rules.classify("You charged twice on my card, I want a refund")
    assert result.intent == Intent.COMPLAINT and "billing" in result.flags


def test_booking_change_flag():
    assert "booking_change" in rules.classify("I need to change my booking dates").flags
    assert "booking_change" not in rules.classify("Do you have rooms available in March?").flags


@pytest.mark.parametrize("text,late", [("after 11pm", True), ("at 23:00", True), ("poslije 22h", True),
                                       ("at 3pm", False), ("2 adults", False), ("u 14h", False)])
def test_late_time_parsing(text, late):
    assert mentions_late_time(fold(text)) is late


def test_hybrid_uses_llm_for_non_safety_intents():
    llm = ScriptedLLM()
    llm.queue({"intent": "SERVICE_REQUEST", "confidence": 0.9, "request_type": "restaurant"})
    result = HybridIntentClassifier(llm).classify("We'd love a quiet corner for our anniversary tonight")
    assert result.intent == Intent.SERVICE_REQUEST
    assert result.request_type == RequestType.RESTAURANT
    assert result.source == "llm"


def test_hybrid_safety_intents_bypass_llm():
    llm = ScriptedLLM()
    result = HybridIntentClassifier(llm).classify("Call an ambulance, my husband is unconscious")
    assert result.intent == Intent.EMERGENCY
    assert llm.calls == []


@pytest.mark.parametrize("bad", ["not json at all", '{"intent": "NOT_AN_INTENT", "confidence": 0.9}',
                                 '{"intent": "COMPLAINT", "confidence": 0.1}', LLMError("timeout")])
def test_hybrid_falls_back_to_rules_on_bad_llm_output(bad):
    llm = ScriptedLLM([bad])
    result = HybridIntentClassifier(llm).classify("What time is breakfast?")
    assert result.intent == Intent.HOTEL_INFORMATION
    assert result.source == "rules"
