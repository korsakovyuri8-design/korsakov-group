"""Local concierge: discovery, plan commands and multi-part trip requests.

Sits next to the transaction dialogue and keeps the states apart:

* DISCOVERY    - "where can I...", "open now", "somewhere lively" ->
                 candidates from the discovery pipeline over structured
                 region data only (never a model's memory); hours, kitchen
                 state, freshness and distance are computed, not guessed.
* SAVE / SHORTLIST / PLAN - "save the second bar", "add the concert to
                 Sunday" -> plan items with a non-transactional status.
                 Nothing is reserved, quoted or confirmed.
* RESERVATION  - "book the first restaurant", "buy tickets for the concert"
                 -> through the marketplace bridge only; the transaction
                 dialogue then quotes and asks for consent. A place or event
                 without an offering is never "booked".
* TRANSACTION / CONFIRMATION - owned by TransactionDialogue and stored state.

A multi-part message ("We arrive Friday around 8pm, four of us. Our transfer
is already booked. Find local food still serving when we arrive. Saturday
we're skiing - somewhere casual for lunch...") is split into independent
items. Statements ("already booked", "we have the guide", "we're skiing")
are trip CONTEXT, never new orders; each request keeps its own status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agent import messages as msg
from app.agent.intents import Intent
from app.clock import Clock, as_utc
from app.db.models import Event, ExternalProvider, ItemStatus, Place, Quote
from app.discovery.engine import Candidate, DiscoveryQuery, DiscoveryResult, EventCandidate, discover_events, run
from app.discovery.nlu import DiscoveryRequest, parse_discovery, understand
from app.discovery.render import L, candidate_line, event_line, rejection_summary
from app.marketplace import bridge
from app.observability import log_event
from app.places.taxonomy import FAMILIES, SUBCATEGORIES, label
from app.schemas.messages import ActionTaken
from app.shared.geo import Point
from app.text import contains_phrase, fold
from app.transactions.catalog import SERVICE_CATALOG, VENUE_CATEGORIES, detect_services, keyword_positions, rental_label
from app.transactions.slots import PROPERTY, local_now, parse_count, parse_places, parse_when
from app.trip import itinerary, preferences, schedule, selection

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn
    from app.transactions.dialogue import PlannedOffer, TransactionDialogue

_SAFETY = {Intent.EMERGENCY, Intent.HUMAN_REQUEST, Intent.COMPLAINT}

PLAN_PHRASES = ["my plan", "our plan", "itinerary", "what am i doing", "what are we doing", "what's planned",
                "what is planned", "whats planned", "what do i have", "what do we have", "my schedule",
                "moj plan", "nas plan", "sta imam", "sta imamo", "sta je u planu", "raspored",
                "мой план", "наш план", "что у меня", "что у нас", "что запланировано", "расписание",
                "что мы делаем"]
SAVED_PHRASES = ["what did i save", "what did we save", "what have i saved", "what have we saved", "my saved",
                 "our saved", "show saved", "saved places", "show my saved", "my shortlist", "our shortlist",
                 "sta sam sacuvao", "sta smo sacuvali", "sacuvan*", "что я сохранил*", "что мы сохранил*",
                 "сохранённ*", "сохраненн*", "мои сохран*"]
ARRIVAL = ["we arrive", "i arrive", "arriving", "we land", "landing", "our flight lands", "we get in",
           "stizemo", "dolazimo", "stizem", "dolazim", "slijecemo",
           "прилетаем", "приезжаем", "прибываем", "прилетаю", "приезжаю"]
WHEN_WE_ARRIVE = ["when we arrive", "when we get there", "when we get in", "when we land", "on arrival",
                  "after we arrive", "kad stignemo", "kada stignemo", "kad mi stignemo", "po dolasku",
                  "когда приедем", "когда мы приедем", "когда прилетим", "когда мы прилетим", "по приезду",
                  "когда доберемся", "когда мы доберемся"]
WEEKDAYS = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "cnr": ["pon", "uto", "sri", "čet", "pet", "sub", "ned"],
            "ru": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]}
# Explicit "outside the property" cues. Without one, a question the property's
# own knowledge pack answers ("Where is the parking?") stays with the pack.
EXTERNAL = ["nearby", "near", "in town", "around here", "close by", "closest", "nearest", "open now", "still open",
            "recommend*", "suggest*", "find", "somewhere", "any good", "local", "in the area", "u blizini", "u gradu",
            "najbliz*", "preporu*", "negdje", "nadji", "pronadji", "otvoreno sada", "рядом", "поблизости", "в городе",
            "ближайш*", "посоветуй*", "порекоменд*", "найди*", "где-нибудь", "местн*"]
# Property knowledge (key / category) that answers the same kind of need as a
# discovery category.
_PROPERTY_TOPICS = [
    ({"restaurant", "breakfast", "dining"}, "FOOD"),
    ({"bar", "dining"}, "NIGHTLIFE"),
    ({"parking", "transport", "airport_transfer"}, "MOBILITY_INFRASTRUCTURE"),
    ({"airport_transfer", "transport"}, "TRANSPORT"),
    ({"spa", "wellness", "amenities"}, "WELLNESS"),
    ({"wifi"}, "CONNECTIVITY"),
    ({"ski_storage"}, "RENTAL"),
    ({"local_durmitor", "local_activities"}, "ATTRACTION"),
    ({"local_durmitor", "local_activities"}, "ACTIVITY"),
    ({"payment_methods", "billing"}, "FINANCIAL_SERVICE"),
]
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")

# Statements of fact about the trip: context, never a new order.
_STATEMENT = [
    re.compile(r"\balready (?:booked|reserved|arranged|have|got|sorted|paid|organi[sz]ed)\b"),
    re.compile(r"\b(?:is|are|was|were|has been|have been) (?:already )?(?:booked|reserved|arranged|sorted|confirmed)\b"),
    re.compile(r"^\W*(?:[a-z]+\s+){0,2}(?:we|i)(?:'re| are| am|'m| will be|'ll be) [a-z]+ing\b"),
    re.compile(r"^\W*(?:[a-z]+\s+){0,2}(?:we|i) (?:have|'ve got|have got|got) (?:the|our|a|an|my)\b"),
    re.compile(r"\bvec (?:smo |sam |je )?(?:rezervis|imamo|imam|dogovor)"),
    re.compile(r"\bimamo (?:vodic|transfer|rezervac)"),
    re.compile(r"\buzhe (?:zabronir|zakaza|est)|\bu nas (?:uzhe |est )?(?:gid|transfer|bron)|\bmy katae"),
]
_REQUEST_CUES = ["find", "book", "get us", "get me", "need", "want", "suggest", "recommend", "can you", "could you",
                 "please", "looking for", "where", "what", "tell me", "is there", "show", "organi*", "arrange",
                 "nadji", "trebam*", "zelim*", "hocemo", "preporu*", "найди*", "нужен", "нужна", "нужно", "хотим",
                 "посоветуй*", "закажи*", "забронируй*"]
_ACTIVITIES = {"ski_resort": ["skiing", "ski", "on the slopes", "skijamo", "skijanje", "skijati", "катаемся на лыжах",
                              "на лыжах", "лыжи", "katamsya na lyzhah"],
               "trailhead": ["hiking", "hike", "trekking", "planinarimo", "в поход*"],
               "lake": ["at the lake", "kod jezera", "на озере"]}

SECTION = {
    "dinner": {"en": "Dinner", "cnr": "Večera", "ru": "Ужин"},
    "lunch": {"en": "Lunch", "cnr": "Ručak", "ru": "Обед"},
    "breakfast": {"en": "Breakfast", "cnr": "Doručak", "ru": "Завтрак"},
    "drinks": {"en": "Drinks", "cnr": "Piće", "ru": "Выпить"},
    "events": {"en": "Events", "cnr": "Događaji", "ru": "События"},
    "food": {"en": "Food", "cnr": "Hrana", "ru": "Еда"},
    "sights": {"en": "Things to see", "cnr": "Šta vidjeti", "ru": "Что посмотреть"},
    "groceries": {"en": "Groceries", "cnr": "Namirnice", "ru": "Продукты"},
}
_TEXT = {
    "eta": {"en": " (estimated arrival at the hotel ~{t}, from the provider's typical journey time)",
            "cnr": " (procijenjeni dolazak u hotel ~{t}, prema uobičajenom trajanju vožnje)",
            "ru": " (ориентировочное прибытие в отель ~{t}, по обычному времени в пути)"},
    "serving_at": {"en": "kitchen open at {t}", "cnr": "kuhinja radi u {t}", "ru": "кухня работает в {t}"},
    "need": {"en": "{label}: {question}", "cnr": "{label}: {question}", "ru": "{label}: {question}"},
    "unsupported": {"en": "{label}: I can't arrange this through a provider here - I can pass it to the staff.",
                    "cnr": "{label}: ovo ne mogu da organizujem preko partnera - mogu proslijediti osoblju.",
                    "ru": "{label}: это я не могу организовать через партнёра — могу передать сотрудникам."},
    "failed": {"en": "{label}: the provider did not answer - staff will follow up. Nothing is booked.",
               "cnr": "{label}: partner nije odgovorio - osoblje će se javiti. Ništa nije rezervisano.",
               "ru": "{label}: партнёр не ответил — сотрудники свяжутся с вами. Ничего не забронировано."},
    "nothing_found": {"en": "{label}: nothing in my local data matches{ctx}{why}.",
                      "cnr": "{label}: ništa u mojim lokalnim podacima ne odgovara{ctx}{why}.",
                      "ru": "{label}: в моих местных данных ничего подходящего{ctx}{why}."},
    "health": {"en": "If it is urgent, call {n}. I can't give medical advice.",
               "cnr": "Ako je hitno, pozovite {n}. Ne mogu davati medicinske savjete.",
               "ru": "Если это срочно, звоните {n}. Медицинских советов я не даю."},
    "health_no_number": {"en": "If it is urgent, call the local emergency number. I can't give medical advice.",
                         "cnr": "Ako je hitno, pozovite lokalni broj za hitne slučajeve. Ne mogu davati medicinske savjete.",
                         "ru": "Если это срочно, звоните по местному экстренному номеру. Медицинских советов я не даю."},
    "or": {"en": "or:", "cnr": "ili:", "ru": "или:"},
    "first_question": {"en": "To price the rest, first: {question}", "cnr": "Za ostalo mi prvo treba: {question}",
                       "ru": "Чтобы рассчитать остальное, сначала: {question}"},
    "synthetic": {"en": "(Demo data - synthetic places.)", "cnr": "(Demo podaci - izmišljena mjesta.)",
                  "ru": "(Демо-данные — вымышленные места.)"},
    # statements / context
    "noted": {"en": "Noted: {text}", "cnr": "Zabilježeno: {text}", "ru": "Принято к сведению: {text}"},
    "in_plan": {"en": " - in your plan: {title} ({status})", "cnr": " - u vašem planu: {title} ({status})",
                "ru": " - в вашем плане: {title} ({status})"},
    "not_in_plan": {"en": " - I don't see it in your plan; I haven't booked anything for it",
                    "cnr": " - ne vidim to u vašem planu; ništa nisam rezervisao",
                    "ru": " - в вашем плане этого нет; я ничего не бронировал"},
    "near_anchor": {"en": " (near {a})", "cnr": " (blizu: {a})", "ru": " (рядом: {a})"},
    "now": {"en": " (now)", "cnr": " (sada)", "ru": " (сейчас)"},
    "evening": {"en": " evening", "cnr": " uveče", "ru": " вечером"},
    "arrival_from_plan": {"en": "arrival estimated from your booked transfer ({t})",
                          "cnr": "dolazak procijenjen prema rezervisanom transferu ({t})",
                          "ru": "прибытие рассчитано по забронированному трансферу ({t})"},
    # commands
    "shortlisted": {"en": "Added to your shortlist: {title}. Nothing is reserved.",
                    "cnr": "Dodato u uži izbor: {title}. Ništa nije rezervisano.",
                    "ru": "Добавлено в список вариантов: {title}. Ничего не забронировано."},
    "planned": {"en": "Added to your plan for {when}: {title} - planned only; nothing is reserved{tickets}.",
                "cnr": "Dodato u plan za {when}: {title} - samo u planu; ništa nije rezervisano{tickets}.",
                "ru": "Добавлено в план на {when}: {title} - только в плане; ничего не забронировано{tickets}."},
    "planned_no_time": {"en": "Added to your plan: {title} - planned only; nothing is reserved.",
                        "cnr": "Dodato u plan: {title} - samo u planu; ništa nije rezervisano.",
                        "ru": "Добавлено в план: {title} - только в плане; ничего не забронировано."},
    "tickets_hint": {"en": " (tickets are sold through a partner - say \"buy tickets for the {what}\" for a price)",
                     "cnr": " (karte prodaje partner - recite \"kupi karte\" za cijenu)",
                     "ru": " (билеты продаёт партнёр - скажите «купи билеты», чтобы узнать цену)"},
    "tickets_elsewhere": {"en": " (tickets are needed but not sold through me)",
                          "cnr": " (potrebne su karte, ali ih ne prodajem)",
                          "ru": " (нужны билеты, но я их не продаю)"},
    "wrong_day": {"en": "{title} is on {when}, not {asked} - I haven't added it.",
                  "cnr": "{title} je {when}, ne {asked} - nisam ga dodao.",
                  "ru": "{title} проходит {when}, а не {asked} — я его не добавил."},
    "no_ticket_needed": {"en": "{title} needs no ticket - nothing to buy. I can add it to your plan.",
                         "cnr": "Za {title} ne treba karta. Mogu ga dodati u plan.",
                         "ru": "Для «{title}» билет не нужен. Могу добавить в план."},
    "tickets_not_sold": {"en": "Tickets for {title} aren't sold through any partner I work with. Nothing is booked.",
                         "cnr": "Karte za {title} ne prodaje nijedan partner sa kojim radim. Ništa nije rezervisano.",
                         "ru": "Билеты на «{title}» не продаются через моих партнёров. Ничего не забронировано."},
    "not_an_event": {"en": "{title} is a place, not an event - there are no tickets to buy.",
                     "cnr": "{title} je mjesto, ne događaj - nema karata.",
                     "ru": "{title} — это место, а не событие: билетов нет."},
    "ref_not_found": {"en": "I couldn't tell which result you mean by \"{ref}\" - nothing was done for it.",
                      "cnr": "Ne znam na koji rezultat mislite pod \"{ref}\" - ništa nisam uradio.",
                      "ru": "Не понял, какой вариант вы имеете в виду («{ref}»), — ничего не сделано."},
    "ref_ambiguous": {"en": "\"{ref}\" matches several results - please say which one (e.g. \"the second {kind}\").",
                      "cnr": "\"{ref}\" odgovara više rezultata - recite koji (npr. \"drugi\").",
                      "ru": "«{ref}» подходит к нескольким вариантам — уточните, какой (например, «второй»)."},
    "saved_header": {"en": "Saved{what}:", "cnr": "Sačuvano{what}:", "ru": "Сохранено{what}:"},
    "saved_empty": {"en": "You haven't saved anything{what} yet.", "cnr": "Još ništa niste sačuvali{what}.",
                    "ru": "Вы пока ничего не сохранили{what}."},
    # preferences
    "pref_noted": {"en": "Noted for future suggestions: {what}. I'll favour it, but it is a preference, not a filter "
                         "- tell me when something is a must.",
                   "cnr": "Zapamtio sam za buduće prijedloge: {what}. Daću prednost, ali to nije filter.",
                   "ru": "Запомнил для будущих рекомендаций: {what}. Буду учитывать, но это предпочтение, "
                         "а не жёсткий фильтр."},
    "pref_used": {"en": "(Also favouring your usual preference: {what}.)",
                  "cnr": "(Uzimam u obzir i vašu uobičajenu želju: {what}.)",
                  "ru": "(Учитываю и ваше обычное предпочтение: {what}.)"},
    # free window
    "window": {"en": "Your free window: {a}-{b}{why}. Only places open through it, where the visit fits:",
               "cnr": "Vaš slobodan period: {a}-{b}{why}. Samo mjesta otvorena cijelo vrijeme:",
               "ru": "Ваше свободное время: {a}-{b}{why}. Только места, открытые всё это время:"},
    "window_before": {"en": " (before {t})", "cnr": " (prije: {t})", "ru": " (до: {t})"},
    "window_no_plan": {"en": " (I don't see {what} in today's plan, so I counted from now)",
                       "cnr": " (ne vidim to u današnjem planu, računam od sada)",
                       "ru": " (в сегодняшнем плане этого нет, считаю от текущего времени)"},
}
_PREF_LABELS = {"vegetarian_options": "vegetarian_options", "vegan_options": "vegan_options",
                "gluten_free_options": "gluten_free_options", "outdoor_seating": "outdoor_seating"}


def day_label(day: date | datetime, locale: str) -> str:
    names = WEEKDAYS.get(locale.split("-")[0], WEEKDAYS["en"])
    name = names[day.weekday()]
    if locale == "cnr-Cyrl":
        name = msg.to_cyrillic_template(name)
    return f"{name} {day.strftime('%d.%m')}"


def T(key: str, locale: str, **kw: object) -> str:
    entry = _TEXT[key]
    text = msg.to_cyrillic_template(entry["cnr"]) if locale == "cnr-Cyrl" else (
        entry.get(locale.split("-")[0]) or entry["en"])
    return text.format(**kw) if kw else text


def _loc(table: dict[str, str], locale: str) -> str:
    if locale == "cnr-Cyrl":
        return msg.to_cyrillic_template(table["cnr"])
    return table.get(locale.split("-")[0]) or table["en"]


def _hits(text: str, phrases: list[str]) -> bool:
    folded = fold(text)
    return any(contains_phrase(folded, p) for p in phrases)


def is_statement(text: str) -> bool:
    """"Our transfer is already booked", "Saturday we're skiing", "Sunday we
    have the guide" - facts about the trip, not requests."""
    folded = fold(text)
    if not any(p.search(folded) for p in _STATEMENT):
        return False
    return not any(contains_phrase(folded, c) for c in _REQUEST_CUES)


_TODAY = ["today", "tonight", "this evening", "this morning", "this afternoon", "danas", "veceras", "jutros",
          "сегодня"]


def explicit_day(text: str, today: date) -> date | None:
    """The day the words NAME. A bare day part ("at night", "in the
    evening") is not a day - it belongs to the day under discussion."""
    parts = parse_when(fold(text), today)
    if parts.day is None:
        return None
    if parts.approximate and parts.day == today and not _hits(text, _TODAY):
        return None
    return parts.day


def _activity(text: str) -> str | None:
    folded = fold(text)
    return next((sub for sub, words in _ACTIVITIES.items() if any(contains_phrase(folded, w) for w in words)), None)


@dataclass
class _TripContext:
    day: date | None = None             # arrival day
    arrival: datetime | None = None     # exact arrival time, if given ("8pm"); not "evening"
    origin: str | None = None           # where they arrive from ("from Podgorica")
    current_day: date | None = None     # the day the conversation is currently about
    activities: dict[date, str] = field(default_factory=dict)   # "Saturday we're skiing" -> ski_resort
    eta: datetime | None = None         # estimated arrival at the property
    eta_note: str | None = None


_DECISION = re.compile(r"^\s*(yes|no|book|confirm|reserve|go ahead|cancel|decline|i'?ll decide|let me think|"
                       r"da\b|ne\b|rezervis|potvrd|otkaz|da\b|net\b|bronir|zabronir|podtver|otmen|"
                       r"да\b|нет\b|брониру|заброниру|подтвер|отмен)")
_DELIMS = re.compile(r"(,\s*(?:and\s+|i\s+|и\s+)?|\s+(?:and|plus|also|as well as|i|kao i|а также|и)\s+)")


def split_requests(text: str) -> list[str]:
    """Sentences, and within a sentence one part per requested service:
    "Get us a transfer, two sets of skis for Saturday, and a guide on
    Sunday." -> three parts. A part keeps the words around its keyword."""
    out: list[str] = []
    for sentence in (x.strip() for x in _SENTENCE.split(text) if x.strip()):
        folded = fold(sentence)
        hits = keyword_positions(folded)
        services = list(dict.fromkeys(svc for _, svc in hits))
        if len(services) < 2:
            out.append(sentence)
            continue
        # fold() lower-cases, strips diacritics and transliterates Cyrillic,
        # so map folded positions back to the original text.
        prefix = [len(fold(sentence[:i])) for i in range(len(sentence) + 1)]

        def original(pos: int) -> int:
            return next((i for i, n in enumerate(prefix) if n >= pos), len(sentence))

        cuts = [0]
        folded_len = len(folded)
        for pos, svc in hits[1:]:
            if svc == services[0] and len(cuts) == 1:
                continue
            delims = [m for m in _DELIMS.finditer(folded, cuts[-1], pos)]
            cut = delims[-1].start() if delims else pos
            if cuts[-1] < cut < folded_len:
                cuts.append(cut)
        cuts = [original(c) for c in cuts] + [len(sentence)]
        parts = [sentence[a:b].strip(" ,") for a, b in zip(cuts, cuts[1:], strict=False)]
        out += [p for p in parts if p]
    return out


@dataclass
class _Section:
    kind: str                              # transaction | discovery | statement
    text: str
    service_type: str | None = None
    request: DiscoveryRequest | None = None
    planned: PlannedOffer | None = None
    results: list[Candidate] = field(default_factory=list)
    events: list[EventCandidate] = field(default_factory=list)
    result: DiscoveryResult | None = None
    at: datetime | None = None
    day: date | None = None
    own_day: date | None = None          # the day this item's own words name
    anchor: tuple[Point, str] | None = None
    note: str | None = None


class Concierge:
    def __init__(self, clock: Clock, transactions: TransactionDialogue | None) -> None:
        self.clock = clock
        self.txn = transactions

    # ------------------------------------------------------------ helpers
    def _now_local(self, turn: _Turn) -> datetime:
        return local_now(self.clock.now(), turn.runtime.timezone)

    def _tz(self, turn: _Turn) -> ZoneInfo:
        return ZoneInfo(turn.runtime.timezone)

    def _fmt(self, turn: _Turn, dt: datetime | None, *, with_time: bool = True) -> str:
        if dt is None:
            return ""
        if dt.tzinfo is not None:
            dt = dt.astimezone(self._tz(turn))
        return day_label(dt, turn.language) + (dt.strftime(" %H:%M") if with_time else "")

    def _party(self, turn: _Turn, text: str) -> int | None:
        return parse_count(fold(text)) or turn.stay.party_size or \
            (turn.stay.facts or {}).get("guest_count", {}).get("value")

    @staticmethod
    def _outcome(turn: _Turn, *kinds: str) -> None:
        for k in kinds:
            if k not in turn.outcomes:
                turn.outcomes.append(k)

    # ========================================================== commands
    def handle_command(self, turn: _Turn) -> bool:
        """Plan views, and plan commands on the last shown results:
        "save 2", "book the first restaurant, save the second bar and add
        the concert to Sunday", "buy tickets for the concert"."""
        if turn.intent is not None and turn.intent.intent in _SAFETY:
            return False
        if _hits(turn.text, SAVED_PHRASES) and not selection.parse_commands(turn.text, self._now_local(turn).date()):
            return self._show_saved(turn)
        entries = turn.state.get("last_results") or []
        commands = selection.parse_commands(turn.text, self._now_local(turn).date())
        if commands and entries:
            resolved = selection.resolve_all(commands, entries)
            if resolved.any_resolved:
                return self._run_commands(turn, resolved.commands)
        if _hits(turn.text, PLAN_PHRASES) and turn.intent is not None and \
                turn.intent.intent not in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST):
            return self._show_plan(turn)
        return False

    def _run_commands(self, turn: _Turn, commands: list[selection.Command]) -> bool:
        lines: list[str] = []
        for c in commands:
            if c.entry is None:
                key = "ref_ambiguous" if c.problem == "ambiguous" else "ref_not_found"
                lines.append(T(key, turn.language, ref=c.ref.text or c.text, kind=c.ref.kind or "one"))
                continue
            turn.reply = None
            if c.verb == "save":
                self._save(turn, c.entry, ItemStatus.SAVED)
            elif c.verb == "shortlist":
                self._save(turn, c.entry, ItemStatus.SHORTLISTED)
            elif c.verb == "plan":
                self._plan_add(turn, c.entry, c.day)
            elif c.verb == "tickets":
                self._tickets(turn, c.entry, c.party, clause=c.text)
            else:
                self._book(turn, c.entry, clause=c.text)
            if turn.reply:
                lines.append(turn.reply)
        turn.reply, turn.succeeded = "\n".join(lines), True
        log_event("plan_commands", conversation_id=turn.conv.id,
                  commands=[(c.verb, c.entry.get("name") if c.entry else None, c.problem) for c in commands])
        return True

    def _entry_target(self, turn: _Turn, entry: dict[str, Any]) -> tuple[Place | None, Event | None]:
        if entry.get("event_id"):
            event = turn.session.get(Event, entry["event_id"])
            return (turn.session.get(Place, event.place_id) if event and event.place_id else None), event
        return turn.session.get(Place, entry.get("place_id")), None

    def _title(self, turn: _Turn, place: Place | None, event: Event | None) -> str:
        if event is not None:
            return event.title.get(turn.language.split("-")[0]) or event.title.get("en") or event.slug
        return place.name if place else ""

    def _save(self, turn: _Turn, entry: dict[str, Any], status: ItemStatus) -> bool:
        """SAVE / SHORTLIST: a plan item with a non-transactional status. No
        quote, no hold, no provider call."""
        place, event = self._entry_target(turn, entry)
        if place is None and event is None:
            return False
        title = self._title(turn, place, event)
        starts = as_utc(event.start_at) if event else (
            datetime.fromisoformat(entry["at"]).replace(tzinfo=self._tz(turn)) if entry.get("at") else None)
        item = itinerary.add(turn.session, stay_id=turn.stay.id, kind=(event.category if event else place.category),
                             title=title, status=status, starts_at=starts,
                             place_id=place.id if place and not event else None, event_id=event.id if event else None,
                             details={"subcategory": place.subcategory if place and not event else None})
        turn.actions.append(ActionTaken(kind="plan_item", id=item.id, detail={"status": status.value, "title": title}))
        turn.reply = msg.t("saved", turn.language, title=title) if status == ItemStatus.SAVED else \
            T("shortlisted", turn.language, title=title)
        turn.succeeded = True
        self._outcome(turn, "SAVE")
        return True

    def _plan_add(self, turn: _Turn, entry: dict[str, Any], day: date | None) -> bool:
        """"Add the concert to Sunday": a PLANNED item. Never buys tickets."""
        place, event = self._entry_target(turn, entry)
        tz = self._tz(turn)
        if event is not None:
            title = self._title(turn, place, event)
            start = as_utc(event.start_at).astimezone(tz)
            if day is not None and start.date() != day:
                turn.reply = T("wrong_day", turn.language, title=title, when=self._fmt(turn, start),
                               asked=day_label(day, turn.language))
                turn.succeeded = True
                self._outcome(turn, "ANSWER")
                return True
            tickets = ""
            if event.ticket_required:
                sold = bridge.options_for(turn.session, event=event, capabilities=turn.runtime.capabilities)
                tickets = T("tickets_hint", turn.language, what=event.category if event.category != "other" else
                            "event") if sold else T("tickets_elsewhere", turn.language)
            item = itinerary.add(turn.session, stay_id=turn.stay.id, kind=event.category, title=title,
                                 status=ItemStatus.PLANNED, starts_at=start, event_id=event.id)
            turn.reply = T("planned", turn.language, when=self._fmt(turn, start), title=title, tickets=tickets)
        else:
            assert place is not None
            when = datetime.fromisoformat(entry["at"]).replace(tzinfo=tz) if entry.get("at") else None
            if day is not None and (when is None or when.date() != day):
                when = None
            item = itinerary.add(turn.session, stay_id=turn.stay.id, kind=place.category, title=place.name,
                                 status=ItemStatus.PLANNED, starts_at=when, place_id=place.id,
                                 details={"day": day.isoformat()} if day and when is None else {})
            turn.reply = T("planned", turn.language, when=self._fmt(turn, when), title=place.name, tickets="") \
                if when else T("planned_no_time", turn.language, title=place.name)
        turn.actions.append(ActionTaken(kind="plan_item", id=item.id, detail={"status": "planned", "title": item.title}))
        turn.succeeded = True
        self._outcome(turn, "SAVE")
        return True

    def _book(self, turn: _Turn, entry: dict[str, Any], *, clause: str | None = None) -> bool:
        """DISCOVERY -> TRANSACTION, through the bridge only."""
        place, event = self._entry_target(turn, entry)
        if event is not None:
            return self._tickets(turn, entry, None, clause=clause)
        if place is None:
            return False
        options = [o for o in bridge.options_for(turn.session, place=place, capabilities=turn.runtime.capabilities)
                   if o.service_type in VENUE_CATEGORIES]
        offering = options[0].offering if options else None
        if offering is not None and self.txn is not None:
            preset: dict[str, Any] = {}
            if entry.get("at"):
                preset["reservation_time"] = datetime.fromisoformat(entry["at"]).replace(
                    tzinfo=self._tz(turn)).isoformat()
            if entry.get("party"):
                preset["party_size"] = entry["party"]
            started = self.txn.try_start_service(turn, offering.service_type, preset=preset, venue=place,
                                                 text=clause)
            if started:
                self._outcome(turn, "TRANSACTION")
            return started
        walkin = msg.t("walk_in_ok", turn.language) if (place.attributes or {}).get("walk_in_supported") else ""
        turn.reply = msg.t("not_bookable", turn.language, title=place.name, walkin=walkin)
        turn.succeeded = True
        self._outcome(turn, "ANSWER")
        return True

    def _tickets(self, turn: _Turn, entry: dict[str, Any], party: int | None, *, clause: str | None = None) -> bool:
        """Event -> ticket Offering, through the bridge. An event without a
        ticket offering is never "booked"."""
        place, event = self._entry_target(turn, entry)
        if event is None:
            turn.reply = T("not_an_event", turn.language, title=place.name if place else "")
            self._outcome(turn, "ANSWER")
            return True
        title = self._title(turn, place, event)
        options = [o for o in bridge.options_for(turn.session, event=event, capabilities=turn.runtime.capabilities)
                   if o.service_type == "event_tickets"]
        if not options or self.txn is None:
            unknown = (event.attributes or {}).get("ticketing") == "unknown"
            turn.reply = T("tickets_not_sold" if event.ticket_required or unknown else "no_ticket_needed",
                           turn.language, title=title)
            self._outcome(turn, "ANSWER")
            return True
        start = as_utc(event.start_at).astimezone(self._tz(turn))
        preset: dict[str, Any] = {"event": title, "_event_id": event.id, "_event_day": start.date().isoformat()}
        if party or entry.get("party"):
            preset["party_size"] = party or entry.get("party")
        started = self.txn.try_start_service(turn, "event_tickets", preset=preset, text=clause)
        if started:
            self._outcome(turn, "TRANSACTION")
        return started

    # ------------------------------------------------------------- views
    def _show_plan(self, turn: _Turn) -> bool:
        now_local = self._now_local(turn)
        folded = fold(turn.text)
        parts = parse_when(folded, now_local.date())
        day = parts.day
        evening = any(contains_phrase(folded, w) for w in ("tonight", "this evening", "veceras", "večeras",
                                                           "вечером", "сегодня вечером"))
        if evening and day is None:
            day = now_local.date()
        entries = itinerary.plan(turn.session, turn.stay.id)
        if day is not None:
            entries = schedule.on_day(entries, day, turn.runtime.timezone, evening=evening)
        ctx = ""
        if day:
            ctx = f" ({day_label(day, turn.language)}{T('evening', turn.language) if evening else ''})"
        self._outcome(turn, "ANSWER")
        if not entries:
            turn.reply, turn.succeeded = msg.t("plan_empty", turn.language, ctx=ctx), True
            return True
        lines = [msg.t("plan_header", turn.language, ctx=ctx)]
        for e in entries:
            when = self._fmt(turn, e.starts_at)
            lines.append(f"- {when + ' · ' if when else ''}{e.item.title} - {msg.status_label(e.status, turn.language)}")
        turn.reply, turn.succeeded = "\n".join(lines), True
        return True

    def _show_saved(self, turn: _Turn) -> bool:
        """"What did I save?", "Show my saved bars" - SAVED and SHORTLISTED
        items, optionally of one kind."""
        rest = re.sub(r"\b(show|list|what|did|have|has|i|we|me|my|our|saved|save|shortlist(ed)?|places?|options?|"
                      r"all|the|pokazi|sta|sam|smo|sacuva\w*|покажи|что|я|мы|мои|наши|сохран\w*)\b", " ",
                      fold(turn.text))
        ref = selection.parse_ref(rest)
        kind = ref.kind if ref.kind not in (None, "any") else None
        out = []
        for e in itinerary.plan(turn.session, turn.stay.id):
            if e.status not in ("saved", "shortlisted"):
                continue
            entry = {"kind": "event" if e.item.event_id else "place", "category": e.item.kind,
                     "event_category": e.item.kind, "subcategory": (e.item.details or {}).get("subcategory")}
            if kind and not selection._matches(entry, kind):
                continue
            out.append(e)
        what = ""
        if kind:
            what = f" ({kind.split(':')[-1].replace('_', ' ')})"
        self._outcome(turn, "ANSWER")
        if not out:
            turn.reply, turn.succeeded = T("saved_empty", turn.language, what=what), True
            return True
        lines = [T("saved_header", turn.language, what=what)]
        for e in out:
            when = self._fmt(turn, e.starts_at)
            lines.append(f"- {e.item.title}{' · ' + when if when else ''} - {msg.status_label(e.status, turn.language)}")
        turn.reply, turn.succeeded = "\n".join(lines), True
        return True

    # ======================================================= preferences
    def _capture_preferences(self, turn: _Turn) -> list[preferences.Stated]:
        stated = preferences.extract(turn.text)
        if stated:
            preferences.save(turn.session, turn.stay.guest_id, stated, turn.guest_message_id)
            log_event("traveler_preference_saved", conversation_id=turn.conv.id, keys=[s.key for s in stated])
        return stated

    def _pref_text(self, turn: _Turn, keys: dict[str, Any]) -> str:
        out = []
        for k, v in keys.items():
            if k in _PREF_LABELS:
                out.append(L(_PREF_LABELS[k], turn.language))
            elif k == "noise_level":
                out.append(L(f"tag:{v}", turn.language) if f"tag:{v}" in ("tag:lively", "tag:quiet") else str(v))
            elif k == "local":
                out.append(L("tag:local", turn.language))
            elif k == "price":
                out.append({"en": "budget-friendly", "cnr": "povoljno", "ru": "недорого"}.get(
                    turn.language.split("-")[0], "budget-friendly"))
            else:
                out.append(k.replace("_", " "))
        return ", ".join(out)

    # ========================================================= discovery
    def handle_discovery(self, turn: _Turn) -> bool:
        if not turn.runtime.region or turn.intent is None:
            return False
        if turn.intent.intent in _SAFETY or turn.intent.intent in (Intent.REQUEST_STATUS, Intent.BOOKING_REQUEST):
            return False
        now_local = self._now_local(turn)
        stated = self._capture_preferences(turn)
        if schedule.is_free_window_request(turn.text):
            if self._free_window(turn, now_local):
                return True
        reqs = [r for r in understand(turn.text, now_local) if self._allowed(turn, r)]
        if not reqs:
            if stated:
                turn.reply = T("pref_noted", turn.language,
                               what=self._pref_text(turn, {s.key: s.value for s in stated}))
                turn.succeeded = True
                self._outcome(turn, "SAVE")
                return True
            return False
        if len(reqs) == 1 and not _hits(turn.text, EXTERNAL) and self._property_answers(turn, reqs[0]):
            return False   # property knowledge first ("Where is the parking?")
        sections = []
        for req in reqs:
            if req.query.party_size is None:
                req.query.party_size = self._party(turn, turn.text)
            sec = _Section("discovery", req.text, request=req)
            self._run_discovery(turn, sec, now_local)
            sections.append(sec)
        lines: list[str] = []
        results_state: list[dict[str, Any]] = []
        if len(sections) == 1:
            sec = sections[0]
            if not sec.results and not sec.events:
                self._no_results(turn, sec)
                return True
            self._render_discovery(turn, sec, lines, results_state, header=True)
        else:
            for sec in sections:
                title = self._section_label(turn, sec)
                if not sec.results and not sec.events:
                    lines.append("• " + self._nothing_line(turn, sec, title))
                    continue
                lines.append(f"• {title}{self._ctx(turn, sec)}{self._anchor_note(turn, sec)}:")
                self._render_discovery(turn, sec, lines, results_state, header=False)
        if not results_state:
            turn.reply, turn.succeeded = "\n".join(lines), True
            self._outcome(turn, "FIND")
            return True
        used = self._used_preferences(sections)
        if used:
            lines.append(T("pref_used", turn.language, what=self._pref_text(turn, used)))
        lines.append(self._footer(turn, sections))
        if any(self._synthetic(s) for s in sections):
            lines.append(T("synthetic", turn.language))
        turn.reply = "\n".join(lines)
        turn.set_state(last_results=results_state)
        turn.grounded, turn.succeeded = True, True
        turn.sources = [f"place:{c.place.slug}" for s in sections for c in s.results] + \
            [f"event:{e.event.slug}" for s in sections for e in s.events]
        self._outcome(turn, "RECOMMEND" if any(s.request and s.request.recommend for s in sections) else "FIND")
        log_event("discovery", conversation_id=turn.conv.id, requests=[s.request.hits for s in sections if s.request],
                  results=[c.place.slug for s in sections for c in s.results],
                  rejected={k: v for s in sections if s.result for k, v in s.result.rejected.items()})
        return True

    def _no_results(self, turn: _Turn, sec: _Section) -> None:
        why = rejection_summary(sec.result.rejected, turn.language) if sec.result else ""
        ctx = self._ctx(turn, sec)
        turn.reply = L("none_because", turn.language, ctx=ctx, why=why) if why else L("none", turn.language, ctx=ctx)
        if turn.runtime.capabilities.can("staff_question"):
            turn.set_state(pending_offer={"kind": "ask_staff", "question": turn.text})
        turn.succeeded = True
        self._outcome(turn, "FIND")

    def _nothing_line(self, turn: _Turn, sec: _Section, title: str) -> str:
        why = rejection_summary(sec.result.rejected, turn.language) if sec.result else ""
        return T("nothing_found", turn.language, label=title, ctx=self._ctx(turn, sec), why=f" ({why})" if why else "")

    @staticmethod
    def _used_preferences(sections: list[_Section]) -> dict[str, Any]:
        used: dict[str, Any] = {}
        for s in sections:
            if s.request is None:
                continue
            for c in s.results:
                for k, v in s.request.query.traveller.items():
                    if (c.place.attributes or {}).get(k) == v:
                        used[k] = v
        return used

    def _free_window(self, turn: _Turn, now_local: datetime) -> bool:
        entries = itinerary.plan(turn.session, turn.stay.id)
        window = schedule.free_window(turn.text, entries, now_local, turn.runtime.timezone)
        if window is None:
            return False
        need_text = schedule.strip_window_words(turn.text)    # "before dinner" is WHEN, not WHAT
        req = parse_discovery(need_text, now_local, require_cue=False)
        if req is None or not (req.query.categories or req.query.subcategories):
            req = req or DiscoveryRequest(query=DiscoveryQuery(region=""), text=turn.text)
            req.query.categories = {"ATTRACTION", "CULTURE", "NATURE", "WELLNESS"}
            req.query.subcategories = {"cafe"}
            req.label = "sights"
        req.events = False
        if not self._allowed(turn, req):
            return False
        q = req.query
        q.at, q.window_end, q.visit_minutes, q.open_at = window.start, window.end, 1, True
        q.open_until = None
        sec = _Section("discovery", turn.text, request=req)
        self._run_discovery(turn, sec, now_local)
        why = T("window_before", turn.language, t=window.anchor) if window.anchor else ""
        if not window.from_plan and re.search(r"before|prije|do |перед|до ", fold(turn.text)):
            why = T("window_no_plan", turn.language, what=fold(turn.text).split("before")[-1].strip(" ?.!") or "it")
        self._outcome(turn, "FIND")
        if not sec.results:
            self._no_results(turn, sec)
            return True
        lines = [T("window", turn.language, a=window.start.strftime("%H:%M"), b=window.end.strftime("%H:%M"), why=why)]
        results_state: list[dict[str, Any]] = []
        self._render_discovery(turn, sec, lines, results_state, header=False)
        lines.append(self._footer(turn, [sec]))
        if self._synthetic(sec):
            lines.append(T("synthetic", turn.language))
        turn.reply = "\n".join(lines)
        turn.set_state(last_results=results_state)
        turn.grounded, turn.succeeded = True, True
        turn.sources = [f"place:{c.place.slug}" for c in sec.results]
        return True

    @staticmethod
    def _allowed(turn: _Turn, req: DiscoveryRequest) -> bool:
        """Discovery capabilities: drop categories this property does not
        expose; nothing left = not a discovery turn here."""
        caps = turn.runtime.capabilities
        if not caps.can_discover():
            return False
        q = req.query
        q.categories = {c for c in q.categories if caps.can_discover(c)}
        q.subcategories = {s for s in q.subcategories
                           if s in SUBCATEGORIES and caps.can_discover(SUBCATEGORIES[s].category.value)}
        if req.events and not caps.can_discover_events():
            req.events = False
        return bool(q.categories or q.subcategories or req.events)

    @staticmethod
    def _property_answers(turn: _Turn, req: DiscoveryRequest) -> bool:
        """The property's own pack has a grounded answer about the same kind
        of thing (its parking, its restaurant) - not merely a keyword overlap
        ("after midnight" vs. the late-arrival policy)."""
        retrieval = turn.runtime.knowledge.search(turn.text, turn.language, k=1)
        if not retrieval.grounded:
            return False
        item = retrieval.items[0]
        cats = set(req.query.categories) | {SUBCATEGORIES[s].category.value for s in req.query.subcategories
                                            if s in SUBCATEGORIES}
        related = {cat for words, cat in _PROPERTY_TOPICS if {item.key, item.category, item.topic} & words}
        return bool(related & cats) or bool({item.key, item.topic} & req.query.subcategories)

    @staticmethod
    def _synthetic(section: _Section) -> bool:
        return any(c.place.is_synthetic for c in section.results) or any(e.event.is_synthetic for e in section.events)

    def _resolve_anchor(self, turn: _Turn, text: str) -> tuple[Point, str] | None:
        """"near the Black Lake" -> that place's coordinates, from the region
        data only. Unknown anchor -> None (the caller says so; no guessing)."""
        target = fold(text)
        rows = turn.session.scalars(select(Place).where(Place.region == turn.runtime.region, Place.active))
        best = None
        for p in rows:
            name = fold(p.name)
            if p.latitude is None:
                continue
            if target in name or all(w in name for w in target.split() if len(w) > 2):
                if best is None or len(p.name) < len(best.name):
                    best = p
        return (Point(best.latitude, best.longitude), best.name) if best else None

    def _anchor_for_activity(self, turn: _Turn, subcategory: str) -> tuple[Point, str] | None:
        p = turn.session.scalar(select(Place).where(Place.region == turn.runtime.region, Place.active,
                                                    Place.subcategory == subcategory).order_by(Place.slug).limit(1))
        return (Point(p.latitude, p.longitude), p.name) if p is not None and p.latitude is not None else None

    def _run_discovery(self, turn: _Turn, section: _Section, now_local: datetime) -> None:
        req = section.request
        assert req is not None
        q = req.query
        q.region = turn.runtime.region or ""
        if turn.runtime.location:
            q.near = Point(*turn.runtime.location)
        anchor = None
        if req.anchor_text:
            anchor = self._resolve_anchor(turn, req.anchor_text)
        anchor = anchor or section.anchor
        if anchor is not None:
            q.near, q.anchor_label = anchor
            section.anchor = anchor
            if q.max_km == 2.0:
                q.max_km = None     # "near the lake": nearest to the anchor, not 2 km from the hotel
        if section.at is not None:
            q.at = section.at.replace(tzinfo=None)
            if not q.serving_at and q.open_until is None:
                q.open_at = True
        if req.essential and q.max_km is None:
            q.limit = 3
        stored = preferences.load(turn.session, turn.stay.guest_id)
        if stored:
            attrs, price, tags = preferences.as_ranking(stored)
            q.traveller = {k: v for k, v in attrs.items() if k not in q.required and k not in q.preferred}
            q.price_pref = q.price_pref or price
            q.tags_preferred |= tags
        now_utc = self.clock.now()
        if q.subcategories or q.categories:
            signals = bridge.ranking_signals(turn.session, property_id=turn.runtime.property_id,
                                             capabilities=turn.runtime.capabilities)
            section.result = run(turn.session, q, now_utc, turn.runtime.timezone, signals=signals)
            section.results = section.result.candidates
        if req.events:
            tz = self._tz(turn)
            if req.event_window is not None:
                start = req.event_window[0].replace(tzinfo=tz)
                end = req.event_window[1].replace(tzinfo=tz)
            elif q.at is not None:
                start = datetime.combine(q.at.date(), time(0, 0), tzinfo=tz)
                end = start + timedelta(days=1)
            else:
                start, end = now_utc, now_utc + timedelta(days=7)
            tags = {t for t in q.relevance_tags if t in ("jazz",)} or None
            section.events = discover_events(turn.session, q.region, now_utc, start, end, tags=tags,
                                             categories=req.event_categories or None, limit=3, min_age=q.min_age,
                                             near=q.near)

    def _ctx(self, turn: _Turn, section: _Section) -> str:
        req = section.request
        q = req.query if req else None
        if req is not None and req.essential and q is not None and (q.at is None or not req.time_explicit):
            return ""
        if req is not None and req.events and not (q and (q.categories or q.subcategories)) and req.event_window:
            start, end = req.event_window
            evening = start.hour >= 17
            return L("ctx_at", turn.language, t=day_label(start, turn.language) +
                     (T("evening", turn.language) if evening else ""))
        if q is None or q.at is None:
            return ""
        return L("ctx_at", turn.language, t=self._fmt(turn, q.at))

    def _anchor_note(self, turn: _Turn, section: _Section) -> str:
        if section.anchor is None:
            return ""
        return T("near_anchor", turn.language, a=section.anchor[1])

    def _render_discovery(self, turn: _Turn, section: _Section, lines: list[str],
                          results_state: list[dict[str, Any]], *, header: bool) -> None:
        req = section.request
        assert req is not None
        q = req.query
        ctx = self._ctx(turn, section)
        anchor_name = q.anchor_label or {"en": "the hotel", "cnr": "hotela", "ru": "отеля"}.get(
            turn.language.split("-")[0], "the hotel")
        if header and section.results:
            if req.essential:
                what = ", ".join(dict.fromkeys(label(s, turn.language) for s in sorted(q.subcategories)))
                lines.append(L("header_essential", turn.language, what=what, ctx=ctx))
            else:
                lines.append(L("header", turn.language, ctx=ctx + self._anchor_note(turn, section)))
        at_iso = q.at.isoformat() if q.at else None
        section_label = self._section_label(turn, section)
        from app.places.hours import OpenState

        for c in section.results:
            n = len(results_state) + 1
            line = candidate_line(n, c, turn.language, anchor=anchor_name, now=q.at is None)
            if q.at is not None and q.serving_at and c.kitchen is not None and c.kitchen.state == OpenState.OPEN:
                line += "; " + T("serving_at", turn.language, t=q.at.strftime("%H:%M"))
            lines.append(line)
            results_state.append({"kind": "place", "place_id": c.place.id, "category": c.place.category,
                                  "subcategory": c.place.subcategory, "name": c.place.name, "at": at_iso,
                                  "party": q.party_size, "section": section_label})
        if section.events:
            if section.results or header:
                lines.append(L("events_header", turn.language, ctx=ctx))
            tz = self._tz(turn)
            for e in section.events:
                n = len(results_state) + 1
                lines.append(event_line(n, e, turn.language, lambda dt: self._fmt(turn, as_utc(dt).astimezone(tz))))
                results_state.append({"kind": "event", "event_id": e.event.id, "event_category": e.event.category,
                                      "name": e.event.title.get("en") or e.event.slug,
                                      "at": as_utc(e.event.start_at).astimezone(tz).replace(tzinfo=None).isoformat(),
                                      "party": q.party_size, "section": "events"})
        cats = {c.place.category for c in section.results} | (
            {SUBCATEGORIES[s].category.value for s in q.subcategories if s in SUBCATEGORIES})
        if "HEALTH" in cats:
            number = turn.runtime.emergency_number
            lines.append(T("health", turn.language, n=number) if number else T("health_no_number", turn.language))

    def _footer(self, turn: _Turn, sections: list[_Section]) -> str:
        bookable = any(self._bookable(turn, c.place) for s in sections for c in s.results)
        return L("footer", turn.language, book=L("footer_book", turn.language) if bookable else "")

    def _bookable(self, turn: _Turn, place: Place) -> bool:
        return any(o.service_type in VENUE_CATEGORIES
                   for o in bridge.options_for(turn.session, place=place, capabilities=turn.runtime.capabilities))

    # ======================================================= trip planner
    def handle_plan(self, turn: _Turn) -> bool:
        """A message with several independent requests (and statements of
        trip context) -> independent plan items."""
        if self.txn is None or turn.intent is None or turn.intent.intent in _SAFETY:
            return False
        segments = split_requests(turn.text)
        if len(segments) < 2 or self._answers_open_offers(turn):
            return False
        now_local = self._now_local(turn)
        tz = self._tz(turn)
        sections: list[_Section] = []
        ctx = _TripContext()
        for seg in segments:
            folded = fold(seg)
            parts = parse_when(folded, now_local.date())
            if _hits(seg, ARRIVAL) and ctx.day is None:
                ctx.day = parts.day
                if parts.day and parts.time and not parts.approximate:
                    ctx.arrival = datetime.combine(parts.day, parts.time, tzinfo=tz)
                places, _ = parse_places(seg)
                ctx.origin = places.get("pickup")
            if (named := explicit_day(seg, now_local.date())) is not None:
                ctx.current_day = named
            services = [s for s in detect_services(seg) if s not in VENUE_CATEGORIES]
            if is_statement(seg):
                day = named or (ctx.day if services and SERVICE_CATALOG[services[0]].domain == "transport"
                                    else ctx.current_day)
                if (act := _activity(seg)) and day:
                    ctx.activities[day] = act
                sections.append(_Section("statement", seg, service_type=services[0] if services else None, day=day))
                continue
            if services:
                sections.append(_Section("transaction", seg, service_type=services[0]))
                continue
            if not turn.runtime.region:
                continue
            for req in understand(seg, now_local):
                if not self._allowed(turn, req):
                    continue
                own_day = explicit_day(req.text, now_local.date())
                day = own_day or (None if req.essential else ctx.current_day)
                sections.append(_Section("discovery", req.text, request=req, day=day, own_day=own_day))
        actionable = [s for s in sections if s.kind != "statement"]
        statements = [s for s in sections if s.kind == "statement"]
        # One request on its own stays with its own handler; it comes here
        # only as DISCOVERY that needs the stated trip context ("Saturday
        # we're skiing. Find somewhere casual for lunch.").
        if len(actionable) < 2 and not (statements and any(s.kind == "discovery" for s in actionable)):
            return False
        party = self._party(turn, turn.text)
        log_event("trip_plan", conversation_id=turn.conv.id,
                  items=[s.service_type or (s.request.hits if s.request else s.kind) for s in sections])
        plan_entries = itinerary.plan(turn.session, turn.stay.id)
        eta = ctx.arrival
        for sec in sections:
            if sec.kind == "statement":
                sec.note, found_eta = self._statement_note(turn, sec, plan_entries, ctx)
                eta = found_eta or eta
        for sec in sections:
            if sec.kind != "transaction":
                continue
            preset, partial, inferred = self._preset(sec, ctx, party)
            sec.planned = self.txn.prepare(turn, sec.service_type, sec.text, preset=preset, preset_partial=partial,
                                           inferred=inferred)
            if sec.service_type in ("airport_transfer", "taxi") and sec.planned.outcome == "offered" and ctx.arrival:
                minutes = (sec.planned.provider.config or {}).get("estimated_duration_minutes")
                if minutes:
                    eta = ctx.arrival + timedelta(minutes=int(minutes))
                    sec.at = eta
        for sec in sections:
            if sec.kind != "discovery":
                continue
            assert sec.request is not None
            q = sec.request.query
            q.party_size = q.party_size or party
            if _hits(sec.text, WHEN_WE_ARRIVE) and (eta is not None or ctx.day is not None):
                sec.at = eta or (datetime.combine(ctx.day, time(20, 0), tzinfo=tz) if ctx.day else None)
                sec.request.time_explicit = True
                if not q.serving_at and sec.request.label in ("food", "lunch", None):
                    q.serving_at = True
            elif sec.day is not None and sec.own_day is None and not sec.request.essential:
                self._inherit_day(sec, sec.day, now_local.date())
            day = (q.at.date() if q.at else None) or sec.day
            daytime = q.at is None or q.at.hour < 17          # skiing / hiking happen by day
            if day and day in ctx.activities and daytime and not sec.request.anchor_text \
                    and not sec.request.essential:
                sec.anchor = self._anchor_for_activity(turn, ctx.activities[day])
            self._run_discovery(turn, sec, now_local)
        return self._compose(turn, sections)

    @staticmethod
    def _inherit_day(sec: _Section, day: date, today: date) -> None:
        """"Saturday we're skiing. Find somewhere casual for lunch." -> lunch
        on Saturday; "At night ... cocktails" -> that night."""
        from app.discovery.nlu import _default_time

        req = sec.request
        assert req is not None
        q = req.query
        folded = fold(req.text)
        shift = day - (q.at.date() if q.at is not None else today)
        when = parse_when(folded, day)
        t = when.time if when.time and not when.approximate else None
        if t is None:
            if any(contains_phrase(folded, w) for w in ("at night", "tonight", "night", "late", "noc*", "ночь*",
                                                        "ночью")):
                t = time(22, 0)
            elif any(contains_phrase(folded, w) for w in ("evening", "uvece", "вечер*")):
                t = time(19, 0) if req.label != "drinks" else time(22, 0)
            else:
                t = _default_time(req.label)
        q.at = datetime.combine(day, t)
        if q.open_until is not None:          # "still open after midnight" - that night, not tonight
            q.open_until += shift
        if q.window_end is not None:
            q.window_end += shift
        if req.label in ("lunch", "food", "breakfast"):
            q.serving_at, q.open_at = True, False
        elif q.open_until is None:
            q.open_at = True
        if req.events:
            start_h = 17 if any(contains_phrase(folded, w) for w in ("evening", "tonight", "night", "uvece",
                                                                      "вечер*")) else 0
            req.event_window = (datetime.combine(day, time(0)) + timedelta(hours=start_h),
                                datetime.combine(day, time(0)) + timedelta(hours=24))

    def _statement_note(self, turn: _Turn, sec: _Section, entries: list[itinerary.PlanEntry],
                        ctx: _TripContext) -> tuple[str, datetime | None]:
        """A statement about the trip, checked against the plan. Returns the
        line and, for a booked transfer, the estimated arrival time."""
        text = T("noted", turn.language, text=sec.text.strip().rstrip("."))
        if sec.service_type is None:
            return text, None
        domain = SERVICE_CATALOG[sec.service_type].domain
        tz = self._tz(turn)
        match = None
        for e in entries:
            if e.item.quote_id is None or e.status in ("expired", "cancelled", "rejected", "failed"):
                continue
            quote = turn.session.get(Quote, e.item.quote_id)
            if quote is None or SERVICE_CATALOG.get(quote.service_type) is None or \
                    SERVICE_CATALOG[quote.service_type].domain != domain:
                continue
            if sec.day is not None and e.starts_at is not None and e.starts_at.astimezone(tz).date() != sec.day:
                continue
            match = (e, quote)
            break
        if match is None:
            return text + T("not_in_plan", turn.language), None
        e, quote = match
        line = text + T("in_plan", turn.language, title=self._fmt(turn, e.starts_at),
                        status=msg.status_label(e.status, turn.language))
        eta = None
        if domain == "transport" and e.starts_at is not None and e.status not in ("offered",):
            provider = turn.session.get(ExternalProvider, quote.provider_id)
            minutes = (provider.config or {}).get("estimated_duration_minutes") if provider else None
            if minutes:
                eta = e.starts_at.astimezone(tz) + timedelta(minutes=int(minutes))
                line += "; " + T("arrival_from_plan", turn.language, t=eta.strftime("~%H:%M"))
        return line, eta

    def _answers_open_offers(self, turn: _Turn) -> bool:
        """"Book the transfer and the guide. Skis later." is a decision about
        open offers, not a new multi-part request."""
        from app.transactions.service import TransactionService

        assert self.txn is not None
        if not TransactionService(turn.session, self.txn.deps).open_quotes(turn.conv.id):
            return False
        return any(_DECISION.match(fold(s)) for s in _SENTENCE.split(turn.text) if s.strip())

    @staticmethod
    def _preset(sec: _Section, ctx: _TripContext,
                party: int | None) -> tuple[dict[str, Any], dict[str, str], list[str]]:
        """Trip context fills only what an item lacks, and every inferred
        value is shown in the offer the guest must accept."""
        preset: dict[str, Any] = {}
        partial: dict[str, str] = {}
        inferred: list[str] = []
        spec = SERVICE_CATALOG[sec.service_type or ""]
        keys = {f.key: f.kind for f in spec.fields}
        if party and "party_size" in keys:
            preset["party_size"] = party
        if party and "quantity" in keys:
            preset["quantity"] = party          # "we want skis" for a group of 4 -> 4 sets (shown, confirmable)
            inferred.append("quantity")
        if spec.domain == "transport" and (ctx.arrival is not None or ctx.day is not None):
            when_key = next((k for k, kind in keys.items() if kind == "datetime"), None)
            if when_key and not parse_when(fold(sec.text), (ctx.day or date.today())).time:
                if ctx.arrival is not None:
                    preset[when_key] = ctx.arrival.isoformat()
                    inferred.append(when_key)
                elif ctx.day is not None:
                    partial[when_key] = ctx.day.isoformat()     # "Friday evening": ask the exact time
            if ctx.origin and "pickup" in keys:
                preset["pickup"] = ctx.origin
                inferred.append("pickup")
            if "destination" in keys:
                preset["destination"] = PROPERTY
                inferred.append("destination")
        return preset, partial, inferred

    def _section_label(self, turn: _Turn, sec: _Section) -> str:
        from app.actions.catalog import action_label

        if sec.service_type == "rental" and sec.planned is not None and sec.planned.draft is not None \
                and sec.planned.draft["values"].get("category"):
            return rental_label(sec.planned.draft["values"]["category"], turn.language)
        if sec.service_type == "rental" and sec.planned is not None and sec.planned.quote is not None \
                and sec.planned.quote.request.get("category"):
            return rental_label(sec.planned.quote.request["category"], turn.language)
        if sec.service_type:
            return action_label(sec.service_type, turn.language)
        req = sec.request
        q = req.query if req else None
        if q is not None and req is not None:
            if req.label == "lunch":
                return _loc(SECTION["lunch"], turn.language)
            if req.label == "breakfast":
                return _loc(SECTION["breakfast"], turn.language)
            if req.label in SECTION and req.label not in ("food",):
                return _loc(SECTION[req.label], turn.language)
            if req.label == "food" or q.subcategories & FAMILIES.get("restaurant", set()):
                if q.at is not None and q.at.hour < 16:
                    return _loc(SECTION["lunch"], turn.language)
                return _loc(SECTION["dinner"], turn.language)
            if "NIGHTLIFE" in q.categories or q.subcategories & FAMILIES.get("bar", set()):
                return _loc(SECTION["drinks"], turn.language)
            if q.subcategories:
                return ", ".join(dict.fromkeys(label(s, turn.language) for s in sorted(q.subcategories)))
        return _loc(SECTION["events"], turn.language)

    def _compose(self, turn: _Turn, sections: list[_Section]) -> bool:
        assert self.txn is not None
        lines = [msg.t("trip_header", turn.language)]
        results_state: list[dict[str, Any]] = []
        codes: list[str] = []
        drafts: list[dict[str, Any]] = []
        shown: list[_Section] = []
        for sec in sections:
            if sec.kind == "statement":
                lines.append("• " + (sec.note or T("noted", turn.language, text=sec.text)))
                continue
            title = self._section_label(turn, sec)
            if sec.kind == "transaction":
                p = sec.planned
                assert p is not None
                if p.outcome == "offered" and p.quote is not None:
                    for i, quote in enumerate(p.quotes or [p.quote]):
                        prefix = f"• {title}: " if i == 0 else "  " + T("or", turn.language) + " "
                        provider = turn.session.get(ExternalProvider, quote.provider_id)
                        line = prefix + (f"{provider.name}: " if provider else "") + \
                            self.txn.offer_line(turn, quote, with_label=False)
                        if sec.at is not None and sec.service_type in ("airport_transfer", "taxi"):
                            line += T("eta", turn.language, t=sec.at.strftime("%H:%M"))
                        conditions = self.txn._conditions(turn, quote).strip()
                        if conditions:
                            line += f". {conditions}"
                        lines.append(line)
                        codes.append(quote.code)
                    self._outcome(turn, "TRANSACTION")
                elif p.outcome == "missing" and p.draft is not None:
                    lines.append("• " + T("need", turn.language, label=title, question=self.txn.question(turn, p.draft)))
                    drafts.append(p.draft)
                elif p.outcome == "unavailable":
                    lines.append(f"• {p.detail}")
                elif p.outcome == "failed":
                    lines.append("• " + T("failed", turn.language, label=title))
                else:
                    lines.append("• " + T("unsupported", turn.language, label=title))
                continue
            q = sec.request.query if sec.request else None
            ctx = self._ctx(turn, sec)
            if sec.request is not None and sec.request.essential and not ctx:
                ctx = T("now", turn.language)
            if not sec.results and not sec.events:
                lines.append("• " + self._nothing_line(turn, sec, title))
                continue
            lines.append(f"• {title}{ctx}{self._anchor_note(turn, sec)}:")
            self._render_discovery(turn, sec, lines, results_state, header=False)
            shown.append(sec)
            if sec.results and not (sec.request and sec.request.essential):
                first = sec.results[0].place
                names = ", ".join(c.place.name for c in sec.results[:1]) + (
                    f" (+{len(sec.results) - 1})" if len(sec.results) > 1 else "")
                itinerary.add(turn.session, stay_id=turn.stay.id, kind=first.category, title=f"{title}: {names}",
                              status=ItemStatus.SHORTLISTED,
                              starts_at=q.at.replace(tzinfo=self._tz(turn)) if q and q.at else None,
                              details={"options": [c.place.id for c in sec.results],
                                       "subcategory": first.subcategory})
        if shown:
            self._outcome(turn, "RECOMMEND" if any(s.request and s.request.recommend for s in shown) else "FIND")
        if codes:
            lines.append(msg.t("trip_footer", turn.language, example=codes[0]))
        if drafts:
            # One question at a time; the rest are asked as each is answered.
            turn.set_state(txn_draft=drafts[0], txn_drafts=drafts[1:] or None)
            lines.append(T("first_question", turn.language, question=self.txn.question(turn, drafts[0])))
        if results_state:
            lines.append(self._footer(turn, shown))
            turn.set_state(last_results=results_state)
        if any(s.results and self._synthetic(s) for s in sections):
            lines.append(T("synthetic", turn.language))
        turn.reply = "\n".join(lines)
        turn.succeeded = True
        turn.grounded = True if results_state else turn.grounded
        turn.sources = [f"place:{c.place.slug}" for s in shown for c in s.results] + \
            [f"event:{e.event.slug}" for s in shown for e in s.events]
        return True
