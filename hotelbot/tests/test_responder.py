import pytest

from app.agent.responder import GroundedResponder
from app.knowledge.schemas import RetrievalResult, RetrievedItem
from app.llm.base import LLMError
from tests.conftest import ScriptedLLM


def _retrieval(score=1.0):
    item = RetrievedItem(key="breakfast", category="dining", source="test",
                         content={"en": "Breakfast is 07:30-10:00.", "cnr": "Doručak je 07:30-10:00."},
                         metadata={}, score=score)
    return RetrievalResult(query="When is breakfast?", items=[item], min_score=0.5)


def test_extractive_mode_returns_knowledge_text_in_language():
    answer = GroundedResponder(None, "H").answer(_retrieval(), "cnr")
    assert answer.grounded and answer.text == "Doručak je 07:30-10:00." and answer.sources == ["breakfast"]


def test_ungrounded_retrieval_never_calls_llm():
    llm = ScriptedLLM()
    answer = GroundedResponder(llm, "H").answer(_retrieval(score=0.2), "en")
    assert not answer.grounded and llm.calls == []


def test_llm_answer_accepted_when_cited():
    llm = ScriptedLLM()
    llm.queue({"answerable": True, "answer": "Breakfast runs 07:30 to 10:00.", "sources": ["breakfast"]})
    answer = GroundedResponder(llm, "Demo").answer(_retrieval(), "en")
    assert answer.grounded and answer.text == "Breakfast runs 07:30 to 10:00." and answer.mode == "llm"
    system = llm.calls[0]["system"]
    assert "[breakfast]" in system and "Demo" in system


@pytest.mark.parametrize(
    "response",
    [
        {"answerable": False, "answer": "I don't know", "sources": []},
        {"answerable": True, "answer": "Breakfast is at 7.", "sources": []},                  # uncited
        {"answerable": True, "answer": "Sauna opens at 5.", "sources": ["sauna"]},           # invented source
        {"answerable": True, "answer": "x", "sources": ["breakfast", "spa"]},               # partly invented
        {"answerable": True, "answer": "   ", "sources": ["breakfast"]},
    ],
)
def test_llm_answer_rejected_when_not_grounded(response):
    llm = ScriptedLLM()
    llm.queue(response)
    assert not GroundedResponder(llm, "H").answer(_retrieval(), "en").grounded


@pytest.mark.parametrize("failure", ["total garbage", '{"answer": 1}', LLMError("HTTP 500")])
def test_llm_failure_falls_back_to_extractive(failure):
    llm = ScriptedLLM([failure])
    answer = GroundedResponder(llm, "H").answer(_retrieval(), "en")
    assert answer.grounded and answer.mode == "extractive" and answer.text == "Breakfast is 07:30-10:00."
