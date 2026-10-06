"""Response Authority invariant: statements about real-world state come from
stored action state only; model prose can never upgrade a state."""

import pytest

from app.agent import messages as msg
from app.agent.authority import (
    STATUS_TEMPLATE,
    ClaimLevel,
    detect_claims,
    status_message,
    unbacked_claims,
)
from app.agent.responder import GroundedResponder
from app.db.models import Action, ActionStatus
from app.knowledge.schemas import RetrievalResult, RetrievedItem
from tests.conftest import ScriptedLLM

LOCALES = ["en", "cnr", "cnr-Cyrl", "ru"]
S = ActionStatus

# The strongest claim each status template may make.
ALLOWED_CLAIMS = {
    S.PROPOSED: set(),
    S.SUBMITTED: set(),
    S.PENDING_CONDITION: set(),
    S.SUBMISSION_UNKNOWN: set(),  # outcome unknown: may never read as booked or failed   # provisional: may never read as confirmed
    S.REJECTED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
    S.IN_PROGRESS: set(),
    S.ACCEPTED: {ClaimLevel.ACCEPTED},
    S.COMPLETED: {ClaimLevel.COMPLETED},
}


def _action(status: ActionStatus, action_type: str = "late_checkout_request") -> Action:
    return Action(action_type=action_type, status=status, summary="x", params={})


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("status", list(ActionStatus))
def test_each_status_template_claims_exactly_its_own_state(status, locale):
    text = status_message(_action(status), locale, "Demo Property")
    assert detect_claims(text) <= ALLOWED_CLAIMS[status], (status, locale, text)


@pytest.mark.parametrize("locale", LOCALES)
def test_submitted_never_reads_as_accepted_and_accepted_never_as_completed(locale):
    submitted = status_message(_action(S.SUBMITTED), locale, "P")
    accepted = status_message(_action(S.ACCEPTED), locale, "P")
    assert ClaimLevel.ACCEPTED not in detect_claims(submitted)
    assert ClaimLevel.COMPLETED not in detect_claims(accepted)
    assert submitted != accepted


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("key", ["action_submitted", "action_failed_fallback", "booking_request", "facts_noted",
                                 "capability_unavailable", "cannot_confirm", "question_forwarded"])
def test_operational_templates_make_no_state_claims(key, locale):
    text = msg.t(key, locale, action="late check-out", property="P", facts="x: 1")
    assert detect_claims(text) == set(), (key, locale, text)


def test_every_status_has_a_template_in_every_locale():
    for key in STATUS_TEMPLATE.values():
        for locale in LOCALES:
            assert msg.t(key, locale, action="a", property="p").strip()


@pytest.mark.parametrize(
    "text,level",
    [
        ("Sure, your late checkout is confirmed!", ClaimLevel.ACCEPTED),
        ("I've booked a taxi for you at 6:00.", ClaimLevel.ACCEPTED),
        ("You can stay until 3pm.", ClaimLevel.ACCEPTED),
        ("Your transfer has been arranged.", ClaimLevel.ACCEPTED),
        ("The taxi is on its way.", ClaimLevel.ACCEPTED),
        ("Your upgrade is all set.", ClaimLevel.ACCEPTED),
        ("Vaš kasni check-out je potvrđen.", ClaimLevel.ACCEPTED),
        ("Rezervisali smo vam taksi.", ClaimLevel.ACCEPTED),
        ("Možete ostati do 15h.", ClaimLevel.ACCEPTED),
        ("Ваш поздний выезд подтверждён.", ClaimLevel.ACCEPTED),
        ("Я забронировал вам такси.", ClaimLevel.ACCEPTED),
        ("Your room has been cleaned.", ClaimLevel.COMPLETED),
        ("The heating is fixed now, it's done.", ClaimLevel.COMPLETED),
        ("Klima je popravljena.", ClaimLevel.COMPLETED),
        ("Ремонт выполнен.", ClaimLevel.COMPLETED),
    ],
)
def test_claims_are_detected(text, level):
    assert level in detect_claims(text)


@pytest.mark.parametrize(
    "text",
    [
        "Your request is not confirmed yet.",
        "It has not been approved yet.",
        "Nothing is booked yet.",
        "Zahtjev još nije potvrđen.",
        "Он ещё не подтверждён.",
        "Пока ничего не забронировано.",
        "Breakfast is served from 07:30 to 10:00.",
        "Late check-out depends on availability and must be approved by reception.",
    ],
)
def test_negated_or_neutral_text_is_not_a_claim(text):
    assert detect_claims(text) == set()


def test_claims_are_backed_only_by_matching_stored_state():
    text = "Your late checkout is confirmed."
    assert unbacked_claims(text, []) == {ClaimLevel.ACCEPTED}
    assert unbacked_claims(text, [S.SUBMITTED]) == {ClaimLevel.ACCEPTED}
    assert unbacked_claims(text, [S.ACCEPTED]) == set()
    done = "Your room has been cleaned."
    assert unbacked_claims(done, [S.ACCEPTED, S.IN_PROGRESS]) == {ClaimLevel.COMPLETED}
    assert unbacked_claims(done, [S.COMPLETED]) == set()


def _retrieval():
    item = RetrievedItem(key="checkout", category="check_in_out", source="t", score=1.0, metadata={},
                         content={"en": "Check-out is until 11:00. Late check-out must be approved by reception."})
    return RetrievalResult(query="Can I stay until 3pm?", items=[item], min_score=0.5)


def test_llm_prose_upgrading_a_state_is_replaced_by_property_text():
    llm = ScriptedLLM()
    llm.queue({"answerable": True, "answer": "Sure! Late checkout until 3pm is confirmed.", "sources": ["checkout"]})
    answer = GroundedResponder(llm, "P").answer(_retrieval(), "en", action_statuses=[S.SUBMITTED])
    assert answer.grounded and answer.mode == "extractive"
    assert "confirmed" not in answer.text and "approved by reception" in answer.text


def test_llm_prose_backed_by_state_is_allowed():
    llm = ScriptedLLM()
    llm.queue({"answerable": True, "answer": "Your late check-out is confirmed.", "sources": ["checkout"]})
    answer = GroundedResponder(llm, "P").answer(_retrieval(), "en", action_statuses=[S.ACCEPTED])
    assert answer.mode == "llm" and answer.text == "Your late check-out is confirmed."
