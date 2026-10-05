"""Conversation-level behaviour through Orchestrator.handle() - no HTTP, no WhatsApp."""

import pytest
from sqlalchemy import select

from app.container import build_container
from app.db.models import (
    Conversation,
    ConversationStatus,
    HotelRequest,
    HumanHandoff,
    Message,
    MessageRole,
    RequestType,
    Urgency,
)
from app.schemas.messages import InboundMessage
from tests.conftest import ScriptedLLM


def _all(container, model):
    with container.session_factory() as s:
        return list(s.scalars(select(model)))


def test_grounded_hotel_answer_in_both_languages(chat):
    en = chat("What time is breakfast?")
    assert en.intent == "HOTEL_INFORMATION" and en.grounded and "07:30" in en.text
    assert en.sources == ["breakfast"] and en.knowledge_synthetic

    cnr = chat("Kada je doručak?", guest="guest-2")
    assert cnr.language == "cnr" and cnr.text.startswith("Doručak")


def test_unknown_fact_is_not_invented(chat):
    reply = chat("Do you have a sauna?")
    assert reply.grounded is False
    assert "can't confirm" in reply.text
    assert "sauna" not in reply.text.lower()


def test_late_check_in_creates_request_and_does_not_confirm(chat, container):
    reply = chat("Can we check in after 11pm? We arrive on 20 December, 2 adults.")
    assert reply.intent == "SERVICE_REQUEST"
    assert [a.kind for a in reply.actions] == ["hotel_request"]
    assert "not confirmed" in reply.text
    assert "22:00" in reply.text  # quotes the (synthetic) policy it was grounded on

    [req] = _all(container, HotelRequest)
    assert req.request_type == RequestType.LATE_CHECK_IN
    assert req.details["guest_message"].startswith("Can we check in")
    facts = req.details["guest_stated_facts"]
    assert facts["guest_count"]["value"] == 2 and facts["guest_count"]["confirmed"] is False
    assert facts["arrival_date"]["value"].endswith("-12-20")


def test_late_check_in_in_montenegrin_quotes_policy(chat, container):
    reply = chat("Možemo li se prijaviti poslije 23h? Dolazimo 20.12., nas je dvoje.")
    assert reply.language == "cnr" and reply.sources == ["late_arrival_policy"]
    assert reply.text.startswith("Dolazak poslije 22:00") and "nije potvrđen" in reply.text
    [req] = _all(container, HotelRequest)
    assert req.request_type == RequestType.LATE_CHECK_IN


def test_maintenance_request_is_high_urgency(chat, container):
    chat("Klima ne radi u sobi 12")
    [req] = _all(container, HotelRequest)
    assert req.request_type == RequestType.MAINTENANCE and req.urgency == Urgency.HIGH


def test_information_with_actionable_topic_offers_then_creates_request(chat, container):
    first = chat("Is late check-in possible?")
    assert first.grounded and "send this request" in first.text and not first.actions
    second = chat("yes please")
    assert [a.kind for a in second.actions] == ["hotel_request"]
    [req] = _all(container, HotelRequest)
    assert req.request_type == RequestType.LATE_CHECK_IN
    assert req.details["guest_message"] == "Is late check-in possible?"


def test_offer_can_be_declined(chat, container):
    chat("Do you have a sauna?")
    reply = chat("ne, hvala")
    assert reply.language == "cnr" and reply.text.startswith("U redu")
    assert _all(container, HotelRequest) == []


def test_offer_is_dropped_when_guest_changes_subject(chat, container):
    chat("Do you have a sauna?")
    reply = chat("What time is breakfast?")
    assert reply.grounded and "07:30" in reply.text
    assert _all(container, HotelRequest) == []


def test_unanswerable_question_forwarded_to_staff_on_yes(chat, container):
    chat("Do you have a sauna?")
    reply = chat("da")
    assert "forwarded" in reply.text or "proslijeđeno" in reply.text
    [req] = _all(container, HotelRequest)
    assert req.request_type == RequestType.OTHER and "sauna" in req.summary


@pytest.mark.parametrize(
    "text,reason,urgency,reply_fragment",
    [
        ("I want to talk to a real person", "human_requested", Urgency.NORMAL, "connecting you"),
        ("The room is dirty and the staff was rude!", "complaint", Urgency.HIGH, "sorry to hear"),
        ("You charged my card twice, I want a refund", "billing", Urgency.HIGH, "Billing"),
        ("Fire in room 12!", "emergency", Urgency.CRITICAL, "112"),
        ("I need to cancel my reservation", "booking_modification", Urgency.NORMAL, "cancellations"),
    ],
)
def test_handoff_triggers(chat, container, text, reason, urgency, reply_fragment):
    reply = chat(text)
    assert reply.handed_off
    assert reply_fragment in reply.text
    [handoff] = _all(container, HumanHandoff)
    assert handoff.reason == reason and handoff.urgency == urgency


def test_handoff_package_is_structured(chat, container):
    chat("Hi, we arrive on 20 December, 2 adults")
    chat("I want to speak to a manager")
    [handoff] = _all(container, HumanHandoff)
    pkg = handoff.package
    assert pkg["guest_id"] == "guest-1" and pkg["language"] == "en"
    assert pkg["urgency"] == "normal" and pkg["intent"] == "HUMAN_REQUEST"
    assert pkg["guest_request"] == "I want to speak to a manager"
    assert pkg["guest_stated_facts"]["guest_count"]["value"] == 2
    assert [m["role"] for m in pkg["messages"]] == ["guest", "bot", "guest"]
    assert "unverified" in pkg["summary"] and pkg["conversation_id"]


def test_repeated_failures_escalate(chat, container):
    assert not chat("Do you have a sauna?").handed_off
    reply = chat("Is there a gym?")
    assert reply.handed_off
    [handoff] = _all(container, HumanHandoff)
    assert handoff.reason == "repeated_failure"


def test_success_resets_failure_counter(chat, container):
    chat("Do you have a sauna?")
    chat("What time is breakfast?")
    assert not chat("Is there a gym?").handed_off


def test_bot_stays_silent_while_human_owns_conversation(chat, container):
    chat("I want to talk to a human")
    reply = chat("What time is breakfast?")
    assert reply.text is None and reply.handed_off
    with container.session_factory() as s:
        roles = [m.role for m in s.scalars(select(Message).order_by(Message.seq))]
    assert roles == [MessageRole.GUEST, MessageRole.BOT, MessageRole.GUEST]  # stored for staff


def test_emergency_during_handoff_still_answered_and_escalated(chat, container):
    chat("I want to talk to a human")
    reply = chat("Help, there is a fire in the corridor!")
    assert "112" in reply.text
    [handoff] = _all(container, HumanHandoff)  # same handoff, escalated
    assert handoff.urgency == Urgency.CRITICAL


def test_duplicate_channel_message_is_ignored(chat, container):
    first = chat("What time is breakfast?", channel="whatsapp", external_id="wamid.1")
    again = chat("What time is breakfast?", channel="whatsapp", external_id="wamid.1")
    assert first.text and again.duplicate and again.text is None
    assert len(_all(container, Message)) == 2


def test_language_follows_guest_and_is_persisted(chat, container):
    chat("Hello")
    chat("Da li imate parking?")
    reply = chat("ok")  # no signal: keep Montenegrin
    assert reply.language == "cnr"
    [conv] = _all(container, Conversation)
    assert conv.language == "cnr"


def test_conversations_are_per_guest(chat, container):
    chat("Hi", guest="a")
    chat("Hi", guest="b")
    chat("Breakfast?", guest="a")
    convs = _all(container, Conversation)
    assert len(convs) == 2 and all(c.status == ConversationStatus.ACTIVE for c in convs)


def test_long_messages_are_truncated(chat, container):
    chat("x" * 10_000)
    [msg] = [m for m in _all(container, Message) if m.role == MessageRole.GUEST]
    assert len(msg.text) == container.settings.max_inbound_chars


def test_llm_path_end_to_end(settings, transport):
    llm = ScriptedLLM()
    container = build_container(settings, llm=llm, whatsapp=transport)
    llm.queue(
        {"intent": "HOTEL_INFORMATION", "confidence": 0.9},
        {"answerable": True, "answer": "Breakfast is served 07:30-10:00 in the restaurant.", "sources": ["breakfast"]},
    )
    reply = container.orchestrator.handle(InboundMessage(channel="demo", sender_id="g", text="When can I eat breakfast?"))
    assert reply.grounded and reply.text == "Breakfast is served 07:30-10:00 in the restaurant."
    assert len(llm.calls) == 2


def test_llm_outage_degrades_to_extractive_answer(settings, transport):
    llm = ScriptedLLM()  # every call raises LLMError
    container = build_container(settings, llm=llm, whatsapp=transport)
    reply = container.orchestrator.handle(InboundMessage(channel="demo", sender_id="g", text="When is breakfast?"))
    assert reply.grounded and "07:30" in reply.text


def test_llm_declining_to_answer_means_cannot_confirm(settings, transport):
    llm = ScriptedLLM()
    llm.queue({"intent": "HOTEL_INFORMATION", "confidence": 0.9},
              {"answerable": False, "answer": "", "sources": []})
    container = build_container(settings, llm=llm, whatsapp=transport)
    reply = container.orchestrator.handle(InboundMessage(channel="demo", sender_id="g", text="Is breakfast vegan-friendly?"))
    assert reply.grounded is False and "can't confirm" in reply.text
