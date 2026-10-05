"""Explicit consent for externally binding transactions.

Consent is deliberately strict, because it creates a real obligation:

* The whole message must be an explicit affirmative ("yes", "book it",
  "da, rezervišite", "да, бронируйте"), optionally followed by the offer
  code. "ok", "ok maybe", "sounds good?", "👍" are NOT consent.
* Anything that changes the details ("what about 6:30?") is a modification,
  never consent - the guest is then shown a new offer.
* Consent is scoped to one quote: it counts only if the bot's previous
  message was that offer, or if the guest names the offer code.
  (Enforced by the caller, which knows the dialogue state.)
"""

from __future__ import annotations

import enum
import re

from app.text import fold


class ConsentDecision(str, enum.Enum):
    CONSENT = "consent"
    DECLINE = "decline"
    MODIFY = "modify"
    NONE = "none"          # not a decision about the offer (question, ambiguity...)


_CONSENT_PHRASES = {
    # en
    "yes", "yes please", "yes book it", "yes, book it", "book it", "please book it", "book it please",
    "yes, please book it", "confirm", "i confirm", "yes i confirm", "yes, i confirm", "confirmed", "go ahead and book",
    "yes go ahead", "yes, go ahead", "go ahead and book it", "yes book", "yes, book",
    # cnr
    "da", "da molim", "da, molim", "da, rezervisite", "da rezervisite", "rezervisite", "rezervisi", "da, rezervisi",
    "potvrdjujem", "da, potvrdjujem", "da potvrdjujem", "moze, rezervisite",
    # ru
    "да", "да, пожалуйста", "да пожалуйста", "бронируйте", "да, бронируйте", "да бронируйте", "подтверждаю",
    "да, подтверждаю", "заказывайте", "да, заказывайте",
}
_DECLINE_PHRASES = {
    "no", "no thanks", "no, thanks", "no thank you", "don't book", "do not book", "dont book", "cancel", "not now",
    "never mind", "nevermind", "too expensive",
    "ne", "ne hvala", "ne, hvala", "ne treba", "nemojte", "otkazi", "skupo je",
    "нет", "нет, спасибо", "нет спасибо", "не надо", "не нужно", "отмена", "дорого",
}
_CODE_RE = re.compile(r"\b([A-Z]{4})\b")
_NOISE = re.compile(r"[\s.!,:;\"'()\-–—«»]+")
_CONSENT = {_NOISE.sub(" ", fold(p)).strip() for p in _CONSENT_PHRASES}
_DECLINE = {_NOISE.sub(" ", fold(p)).strip() for p in _DECLINE_PHRASES}


def _normalize(text: str, code: str | None) -> str:
    folded = fold(text)
    if code:
        folded = re.sub(rf"\b(offer|ponuda|predlozenie)?\s*{fold(code)}\b", " ", folded)
    return _NOISE.sub(" ", folded).strip()


def mentioned_code(text: str) -> str | None:
    m = _CODE_RE.search(text)
    return m.group(1) if m else None


def classify(text: str, code: str | None, details_changed: bool) -> ConsentDecision:
    if details_changed:
        return ConsentDecision.MODIFY
    normalized = _normalize(text, code)
    if normalized in _CONSENT:
        return ConsentDecision.CONSENT
    if normalized in _DECLINE:
        return ConsentDecision.DECLINE
    return ConsentDecision.NONE
