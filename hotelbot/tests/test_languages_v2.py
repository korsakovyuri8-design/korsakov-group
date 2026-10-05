"""Iteration 2 language decisions: Cyrillic in -> Cyrillic out; Russian routing."""

import pytest

from app.agent.language import detect_language, resolve_reply_language
from app.agent.messages import CATALOG, t
from app.text import latin_to_cyrillic


@pytest.mark.parametrize("text,locale", [
    ("Када је доручак?", "cnr-Cyrl"),
    ("Гдје је паркинг?", "cnr-Cyrl"),
    ("Во сколько завтрак?", "ru"),
    ("Где можно припарковать машину?", "ru"),
    ("Kada je doručak?", "cnr"),
    ("What time is breakfast?", "en"),
])
def test_locale_detection(text, locale):
    assert resolve_reply_language(detect_language(text), None) == locale


def test_transliteration_keeps_foreign_words_numbers_and_acronyms():
    assert latin_to_cyrillic("Ljubimci nijesu dozvoljeni, 10 EUR, Wi-Fi i check-in.") == \
        "Љубимци нијесу дозвољени, 10 EUR, Wi-Fi и check-in."
    assert latin_to_cyrillic("Džep, Njegoš, Đurđevdan") == "Џеп, Његош, Ђурђевдан"


def test_every_template_exists_in_russian():
    missing = [k for k, v in CATALOG.items() if "ru" not in v]
    assert missing == []


def test_cyrillic_templates_keep_placeholders():
    assert t("action_accepted", "cnr-Cyrl", action="X1", property="Demo") == "Ваш захтјев (X1) је прихваћен (Demo)."


def test_cyrillic_guest_gets_cyrillic_knowledge_answer(chat):
    reply = chat("Када је доручак?")
    assert reply.language == "cnr-Cyrl" and reply.text.startswith("Доручак") and "07:30" in reply.text


def test_cyrillic_guest_gets_cyrillic_action_status(chat):
    reply = chat("Можемо ли да се пријавимо после 23h?")
    assert reply.actions and "није потврђен" in reply.text


@pytest.mark.parametrize("text,intent", [
    ("Пожар в номере, срочно вызовите скорую!", "EMERGENCY"),
    ("Хочу поговорить с живым человеком", "HUMAN_REQUEST"),
    ("В номере грязно, это ужасно", "COMPLAINT"),
    ("Мне списали деньги дважды, верните деньги", "COMPLAINT"),
    ("У меня не работает кондиционер", "SERVICE_REQUEST"),
    ("Можно поздний выезд?", "SERVICE_REQUEST"),
    ("Во сколько завтрак?", "HOTEL_INFORMATION"),
    ("Спасибо!", "GENERAL_CONVERSATION"),
])
def test_russian_routing(chat, text, intent):
    reply = chat(text, guest=f"ru-{hash(text)}")
    assert reply.intent == intent and reply.language == "ru"


def test_russian_safety_and_handoff_replies_are_russian(chat):
    assert "112" in chat("Пожар в номере, срочно вызовите скорую!", guest="r1").text
    assert chat("Хочу поговорить с живым человеком", guest="r2").text.startswith("Конечно")


def test_russian_knowledge_answer_flags_english_source(chat):
    reply = chat("Во сколько завтрак?", guest="r3")
    assert reply.grounded and reply.text.startswith("Эта информация доступна мне только на английском")
