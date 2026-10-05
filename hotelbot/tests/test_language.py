import pytest

from app.agent.language import LanguageGuess, detect_language, resolve_reply_language
from app.text import fold, stem


def test_fold_handles_diacritics_and_cyrillic():
    assert fold("Doručak, ĐAK, šuma") == "dorucak, djak, suma"
    assert fold("Када је доручак?") == "kada je dorucak?"


def test_stem_conflates_montenegrin_inflection():
    assert stem("dorucak") == stem("dorucka") == stem("doruckom")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("What time is breakfast?", "en"),
        ("Can we check in after 11pm?", "en"),
        ("Kada je doručak?", "cnr"),
        ("kada je dorucak", "cnr"),          # no diacritics
        ("Da li imate parking?", "cnr"),
        ("Gdje je recepcija?", "cnr"),
        ("Када је доручак?", "cnr"),          # Cyrillic
        ("Treba mi još peškira", "cnr"),
    ],
)
def test_detects_supported_languages(text, expected):
    assert detect_language(text).language == expected


def test_russian_is_not_mistaken_for_montenegrin_cyrillic():
    guess = detect_language("Во сколько завтрак?")
    # Iteration 2 product decision (Yuri): Russian became a supported locale.
    # Core v1 asserted `guess.language is None` (unsupported) here.
    assert guess.language == "ru" and guess.detected == "ru"


def test_no_signal_keeps_conversation_language():
    guess = detect_language("👍")
    assert guess.language is None
    assert resolve_reply_language(guess, "cnr") == "cnr"
    assert resolve_reply_language(guess, None) == "en"


def test_confident_detection_switches_language():
    assert resolve_reply_language(LanguageGuess("cnr", 0.9, "cnr"), "en") == "cnr"


def test_weak_detection_does_not_flip_language():
    assert resolve_reply_language(LanguageGuess("en", 0.55, "en"), "cnr") == "cnr"
