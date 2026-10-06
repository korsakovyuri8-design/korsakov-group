"""Response composition for the concierge: guest-facing texts (en / cnr / ru),
section labels, day labels and the result section shared by discovery
replies and trip plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING


from app.agent import messages as msg
from app.agent.intents import Intent
from app.discovery.engine import Candidate, DiscoveryResult, EventCandidate
from app.discovery.nlu import DiscoveryRequest
from app.shared.geo import Point
from app.text import contains_phrase, fold

if TYPE_CHECKING:
    from app.transactions.dialogue import PlannedOffer


_SAFETY = {Intent.EMERGENCY, Intent.HUMAN_REQUEST, Intent.COMPLAINT}

WEEKDAYS = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "cnr": ["pon", "uto", "sri", "čet", "pet", "sub", "ned"],
            "ru": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]}

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
