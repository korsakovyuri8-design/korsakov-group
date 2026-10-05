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


_FILLER = {fold(w) for w in """the a an and both please i we us me my our for to also too it them that this
    i a oba obje mi nam molim taj tu to и а оба обе мне нам пожалуйста это эту этот""".split()}
_ALL = {fold(w) for w in "all everything both sve svi oba obje все всё оба обе".split()}
_VERBS = {_NOISE.sub(" ", fold(p)).strip() for p in """yes book confirm reserve go ahead ok
    da rezervisi rezervisite potvrdjujem potvrdi
    да бронируй бронируйте подтверждаю закажи заказывай""".split()}


def classify_selection(text: str, references: list[str]) -> tuple[ConsentDecision, bool]:
    """Consent when several offers are open. Returns (decision, all_requested).

    The message must consist only of a consent verb, references to offers
    (codes or service words, removed by the caller-provided `references`),
    filler words and optionally "all/both". Anything else is not consent."""
    folded = fold(text)
    for ref in sorted(references, key=len, reverse=True):
        folded = re.sub(rf"\b{re.escape(fold(ref).rstrip('*'))}\w*", " ", folded)
    words = [w for w in _NOISE.sub(" ", folded).split() if w]
    all_requested = any(w in _ALL for w in words)
    rest = [w for w in words if w not in _FILLER and w not in _ALL]
    if not rest:
        return (ConsentDecision.CONSENT if all_requested else ConsentDecision.NONE), all_requested
    if all(w in _VERBS for w in rest):
        return ConsentDecision.CONSENT, all_requested
    normalized = " ".join(rest)
    if normalized in _DECLINE:
        return ConsentDecision.DECLINE, all_requested
    return ConsentDecision.NONE, all_requested
