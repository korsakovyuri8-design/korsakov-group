"""Intent classification.

Two classifiers share one result type:

* `RuleIntentClassifier` - weighted multilingual lexicons over
  diacritic-folded text plus a few structural checks (late-time parsing,
  question shape). Deterministic, zero-latency, always available.
* `HybridIntentClassifier` - runs the rules first; safety-critical intents
  found by rules (EMERGENCY, HUMAN_REQUEST) are authoritative. Otherwise an
  LLM classifies with a strict JSON contract, and any malformed/low
  confidence LLM output falls back to the rule result.

The orchestrator adds a third signal: an UNKNOWN message that retrieves
grounded hotel knowledge is treated as HOTEL_INFORMATION.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError

from app.db.models import RequestType
from app.llm.base import LLMError, LLMProvider, extract_json
from app.observability import log_event
from app.text import contains_phrase, fold


class Intent(str, enum.Enum):
    HOTEL_INFORMATION = "HOTEL_INFORMATION"
    LOCAL_RECOMMENDATION = "LOCAL_RECOMMENDATION"
    SERVICE_REQUEST = "SERVICE_REQUEST"
    BOOKING_REQUEST = "BOOKING_REQUEST"
    COMPLAINT = "COMPLAINT"
    EMERGENCY = "EMERGENCY"
    HUMAN_REQUEST = "HUMAN_REQUEST"
    GENERAL_CONVERSATION = "GENERAL_CONVERSATION"
    UNKNOWN = "UNKNOWN"


SAFETY_INTENTS = frozenset({Intent.EMERGENCY, Intent.HUMAN_REQUEST})


@dataclass
class IntentResult:
    intent: Intent
    confidence: float
    request_type: RequestType | None = None
    # Sub-flags that matter to policy: "billing", "booking_change".
    flags: set[str] = field(default_factory=set)
    signals: list[str] = field(default_factory=list)
    source: str = "rules"


class IntentClassifier(Protocol):
    def classify(self, text: str, language: str) -> IntentResult: ...


# --------------------------------------------------------------------------
# Lexicons. Phrases are matched as whole words on folded text; a trailing
# "*" matches any continuation (Montenegrin inflection: "rezervacij*").
# --------------------------------------------------------------------------

EMERGENCY = [
    "emergency", "ambulance", "fire", "heart attack", "can't breathe", "cannot breathe",
    "not breathing", "unconscious", "bleeding", "injured", "gas leak", "call the police",
    "call a doctor", "need a doctor", "overdose", "smoke in the room",
    "hitna pomoc", "hitnu pomoc", "hitan slucaj", "pozar*", "vatra", "zapalil*", "treba mi ljekar",
    "treba nam ljekar", "potreban ljekar", "potreban je ljekar", "zovite ljekara", "krvari*",
    "povrijedj*", "onesvijest*", "ne mogu da disem", "ne dise", "curi gas", "zovite policiju",
]
HUMAN = [
    "human", "real person", "someone real", "live agent", "operator",
    "manager", "receptionist", "talk to someone", "speak to someone", "speak with someone",
    "talk to staff", "speak to staff", "talk to reception", "speak to reception",
    "call me", "not a bot", "stop bot",
    "covjek*", "zivu osobu", "ziva osoba", "pravu osobu", "prava osoba", "menadzer*",
    "recepcioner*", "razgovarati sa nekim", "pricati sa nekim", "razgovaram sa nekim",
    "pozovite me", "nazovite me", "razgovarati sa osobljem", "razgovarati sa recepcijom",
]
COMPLAINT = [
    "complain*", "complaint", "unacceptable", "disappointed", "disappointing", "terrible",
    "awful", "horrible", "dirty", "filthy", "rude", "noisy", "too loud", "disgusting",
    "worst", "not happy", "unhappy", "angry", "bedbugs", "bed bugs", "cockroach*",
    "zalb*", "zalim se", "nezadovolj*", "neprihvatljiv*", "prljav*", "bucn*", "nepristojn*",
    "uzas*", "razocar*", "katastrof*", "bubasvab*", "stjenic*", "odvratn*", "ljut sam",
    "ljuti smo",
]
BILLING = [
    "overcharged", "charged twice", "double charged", "refund", "wrong bill", "wrong invoice",
    "incorrect charge", "billing", "chargeback",
    "naplatili", "naplaceno", "dva puta", "povrat novca", "pogresan racun", "pogresno naplac*",
]
BOOKING = [
    "book a room", "booking", "reservation", "reserve a room", "cancel", "cancellation",
    "change my booking", "modify my booking", "extend my stay", "extra night", "available rooms",
    "room available", "rooms available", "availability", "free rooms",
    "rezerv*", "otkaz*", "otkazat*", "slobodn* sob*", "produzit* boravak", "jos jednu noc",
    "jos jedno nocenje", "dodatn* noc*", "raspolozi*",
]
BOOKING_CHANGE = [
    "cancel", "cancellation", "change my booking", "modify my booking", "change the dates",
    "change my reservation", "otkaz*", "promijen* rezervacij*", "izmijen* rezervacij*",
    "promijen* datum*", "pomjer* rezervacij*",
]
REQUEST_MARKERS = [
    "can you", "could you", "can we", "could we", "can i", "could i", "would it be possible",
    "please", "i need", "we need", "i'd like", "i would like",
    "we would like", "we'd like", "arrange", "send", "bring", "book", "order", "request",
    "mozete li", "mozes li", "mozemo li", "mogu li", "moze li", "molim", "trebam", "treba mi",
    "treba nam", "trebaju nam", "zelim", "zelimo", "htio bih", "htjela bih", "htjeli bismo",
    "posaljite", "donesite", "organizuj*", "dogovor*", "naruci*",
]
REQUEST_TOPICS: dict[RequestType, list[str]] = {
    RequestType.HOUSEKEEPING: [
        "towel*", "clean my room", "clean the room", "cleaning", "fresh sheets", "new sheets",
        "bedding", "pillow*", "blanket*", "toilet paper", "toiletries", "housekeeping", "shampoo",
        "peskir*", "ocist*", "cisce*", "posteljin*", "jastu*", "cebe", "cebad", "toalet papir",
        "sampon*", "sobaric*", "pospremi*",
    ],
    RequestType.MAINTENANCE: [
        "broken", "not working", "doesn't work", "does not work", "no hot water", "leak*",
        "heating", "air conditioning", "no electricity", "power outage", "clogged", "repair",
        "ne radi", "pokvar*", "nema tople vode", "curi", "grijanj*", "klima", "nema struje",
        "zacepi*", "popravi*",
    ],
    RequestType.TRANSPORT: [
        "taxi", "transfer", "pick us up", "pick me up", "pickup", "shuttle", "ride to",
        "taksi", "prevoz", "prijevoz", "da nas pokupi*", "da me pokupi*",
    ],
    RequestType.RESTAURANT: [
        "table for", "reserve a table", "book a table", "room service", "dinner reservation",
        "sto za", "stol za", "rezervis* sto", "rezervac* stola",
    ],
    RequestType.LATE_CHECK_OUT: ["late check-out", "late checkout", "late check out", "kasn* odjav*", "kasniji check-out", "kasniji checkout"],
    RequestType.EARLY_CHECK_IN: ["early check-in", "early checkin", "early check in", "ranij* prijav*", "raniji check-in", "rano stizemo", "ranije stizemo"],
}
ARRIVAL_WORDS = [
    "check in", "check-in", "checkin", "arrive", "arrival", "arriving", "get there", "get to the hotel",
    "prijav*", "dolaz*", "stizem*", "stici", "stizat*", "doci cemo", "docicemo", "dodjemo",
]
LATE_WORDS = ["late", "after midnight", "midnight", "kasn*", "kasno", "ponoc*", "u noci", "nocu"]
RECOMMENDATION = [
    "recommend*", "suggest*", "nearby", "things to do", "what to do", "what to see", "worth visiting",
    "excursion*", "tour*", "hike", "hiking", "sightseeing", "attraction*",
    "preporu*", "u blizini", "sta raditi", "sta da radimo", "sta posjetiti", "sta da posjetimo",
    "sta vidjeti", "izlet*", "obilaz*", "znamenitost*", "planinar*",
]
GREETINGS = [
    "hi", "hello", "hey", "good morning", "good evening", "good afternoon", "thanks", "thank you",
    "bye", "goodbye", "ok", "okay", "great", "perfect", "cheers",
    "zdravo", "cao", "pozdrav", "dobar dan", "dobro jutro", "dobro vece", "hvala", "hvala vam",
    "dovidjenja", "laku noc", "super", "odlicno", "vazi", "u redu",
]
QUESTION_WORDS = [
    "what", "when", "where", "which", "how", "is there", "are there", "do you", "does the",
    "sta", "sto", "kad", "kada", "gdje", "koliko", "kako", "koji", "koja", "koje", "da li",
    "imate li", "ima li", "postoji li",
]

_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm|h|sati|casova)?\b")


def _hits(folded: str, phrases: list[str]) -> list[str]:
    return [p for p in phrases if contains_phrase(folded, p)]


def mentions_late_time(folded: str) -> bool:
    """True if the text names an hour of 22:00 or later (or small hours)."""
    for m in _TIME_RE.finditer(folded):
        hour, suffix = int(m.group(1)), m.group(3)
        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix is None and m.group(2) is None and not re.search(r"(after|poslije|posle|oko|around|at|u)\s+$", folded[: m.start()]):
            continue  # a bare number ("2 adults") is not a time
        if 22 <= hour <= 24 or (suffix != "pm" and 0 <= hour <= 4 and suffix is not None):
            return True
    return False


class RuleIntentClassifier:
    def classify(self, text: str, language: str = "en") -> IntentResult:
        f = fold(text)
        signals: list[str] = []

        def found(name: str, phrases: list[str]) -> bool:
            h = _hits(f, phrases)
            signals.extend(f"{name}:{p}" for p in h)
            return bool(h)

        request_type = self._request_type(f, signals)

        if found("emergency", EMERGENCY):
            return IntentResult(Intent.EMERGENCY, 0.9, signals=signals)
        if found("human", HUMAN):
            return IntentResult(Intent.HUMAN_REQUEST, 0.9, request_type, signals=signals)

        flags: set[str] = set()
        if found("billing", BILLING):
            flags.add("billing")
        if found("complaint", COMPLAINT) or "billing" in flags:
            return IntentResult(Intent.COMPLAINT, 0.8, request_type, flags, signals)
        if found("booking", BOOKING) and request_type not in (RequestType.RESTAURANT, RequestType.TRANSPORT):
            if found("booking_change", BOOKING_CHANGE):
                flags.add("booking_change")
            return IntentResult(Intent.BOOKING_REQUEST, 0.75, RequestType.BOOKING, flags, signals)

        is_request = found("request_marker", REQUEST_MARKERS)
        if request_type is not None and (is_request or request_type == RequestType.MAINTENANCE):
            return IntentResult(Intent.SERVICE_REQUEST, 0.75, request_type, flags, signals)
        if found("recommendation", RECOMMENDATION):
            return IntentResult(Intent.LOCAL_RECOMMENDATION, 0.7, signals=signals)
        if request_type is not None:
            # Topic without an explicit ask ("Is late check-in possible?"): answer
            # from knowledge; the request_type lets the bot offer to act on it.
            return IntentResult(Intent.HOTEL_INFORMATION, 0.6, request_type, flags, signals)
        if found("question", QUESTION_WORDS) or "?" in text:
            return IntentResult(Intent.HOTEL_INFORMATION, 0.55, signals=signals)
        greeting = _hits(f, GREETINGS)
        if greeting and len(f.split()) <= 6:
            signals.extend(f"greeting:{g}" for g in greeting)
            return IntentResult(Intent.GENERAL_CONVERSATION, 0.8, signals=signals)
        return IntentResult(Intent.UNKNOWN, 0.3, signals=signals)

    @staticmethod
    def _request_type(f: str, signals: list[str]) -> RequestType | None:
        arrival = _hits(f, ARRIVAL_WORDS)
        if arrival and (_hits(f, LATE_WORDS) or mentions_late_time(f)) and not _hits(f, REQUEST_TOPICS[RequestType.LATE_CHECK_OUT]):
            signals.append("topic:late_check_in")
            return RequestType.LATE_CHECK_IN
        for rtype, phrases in REQUEST_TOPICS.items():
            h = _hits(f, phrases)
            if h:
                signals.extend(f"topic:{rtype.value}:{p}" for p in h)
                return rtype
        return None


# --------------------------------------------------------------------------
# LLM-assisted classification
# --------------------------------------------------------------------------

class _LLMIntent(BaseModel):
    intent: Intent
    confidence: float
    request_type: RequestType | None = None
    billing_issue: bool = False
    booking_change: bool = False


INTENT_SYSTEM_PROMPT = """You classify guest messages for a hotel concierge system.
Return ONLY a JSON object: {"intent": ..., "confidence": 0..1, "request_type": ... or null,
"billing_issue": bool, "booking_change": bool}

intent is one of:
HOTEL_INFORMATION - a question about the hotel's facilities, times, rules or services
LOCAL_RECOMMENDATION - asks for things to do / places to visit / eat in the area
SERVICE_REQUEST - asks the hotel to DO something (late check-in, towels, cleaning, repair, taxi, table)
BOOKING_REQUEST - new booking, availability, changing or cancelling a reservation
COMPLAINT - dissatisfaction, a problem the guest is unhappy about, billing disputes
EMERGENCY - danger to health, life or property (fire, injury, medical)
HUMAN_REQUEST - wants to talk to a person / staff member
GENERAL_CONVERSATION - greetings, thanks, small talk
UNKNOWN - unclear

request_type (only for SERVICE_REQUEST / BOOKING_REQUEST) is one of:
late_check_in, early_check_in, late_check_out, housekeeping, maintenance, restaurant,
transport, booking, other.
The message may be in English or Montenegrin/Serbian/Croatian/Bosnian."""


class HybridIntentClassifier:
    def __init__(self, llm: LLMProvider | None, rules: RuleIntentClassifier | None = None,
                 min_llm_confidence: float = 0.5) -> None:
        self.llm = llm
        self.rules = rules or RuleIntentClassifier()
        self.min_llm_confidence = min_llm_confidence

    def classify(self, text: str, language: str = "en") -> IntentResult:
        rule_result = self.rules.classify(text, language)
        if rule_result.intent in SAFETY_INTENTS or self.llm is None:
            return rule_result
        try:
            raw = self.llm.complete(
                system=INTENT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
                max_tokens=150,
                temperature=0.0,
            )
            parsed = _LLMIntent.model_validate(extract_json(raw))
        except (LLMError, ValidationError, ValueError) as exc:
            log_event("intent_llm_fallback", reason=type(exc).__name__)
            return rule_result
        if parsed.confidence < self.min_llm_confidence:
            return rule_result
        flags = set()
        if parsed.billing_issue:
            flags.add("billing")
        if parsed.booking_change:
            flags.add("booking_change")
        return IntentResult(
            intent=parsed.intent,
            confidence=parsed.confidence,
            request_type=parsed.request_type or rule_result.request_type,
            flags=flags | rule_result.flags,
            signals=rule_result.signals,
            source="llm",
        )
