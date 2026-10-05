"""Catalogue of generic action types.

An action type is *what* can be done for a guest (independent of property
type). Whether a given property offers it, and *how* it is executed (staff
queue, webhook integration...), is declared in that property's capabilities
(app/capabilities). The intent layer recognises request *topics*
(RequestType); `action_for_topic` maps a topic to an action type.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import RequestType, Urgency
from app.text import latin_to_cyrillic


@dataclass(frozen=True)
class ActionTypeSpec:
    key: str
    topic: RequestType
    labels: dict[str, str]
    default_urgency: Urgency = Urgency.NORMAL


_SPECS = [
    ActionTypeSpec("late_arrival_request", RequestType.LATE_CHECK_IN,
                   {"en": "late arrival", "cnr": "kasni dolazak", "ru": "поздний заезд"}),
    ActionTypeSpec("early_checkin_request", RequestType.EARLY_CHECK_IN,
                   {"en": "early check-in", "cnr": "raniji check-in", "ru": "ранний заезд"}),
    ActionTypeSpec("late_checkout_request", RequestType.LATE_CHECK_OUT,
                   {"en": "late check-out", "cnr": "kasniji check-out", "ru": "поздний выезд"}),
    ActionTypeSpec("housekeeping_request", RequestType.HOUSEKEEPING,
                   {"en": "housekeeping", "cnr": "čišćenje i potrepštine", "ru": "уборка и принадлежности"}),
    ActionTypeSpec("maintenance_request", RequestType.MAINTENANCE,
                   {"en": "repair", "cnr": "popravka", "ru": "ремонт"}, Urgency.HIGH),
    ActionTypeSpec("restaurant_booking", RequestType.RESTAURANT,
                   {"en": "restaurant reservation", "cnr": "rezervacija u restoranu", "ru": "бронирование столика"}),
    ActionTypeSpec("transport_booking", RequestType.TRANSPORT,
                   {"en": "transport", "cnr": "prevoz", "ru": "трансфер"}),
    ActionTypeSpec("booking_inquiry", RequestType.BOOKING,
                   {"en": "booking request", "cnr": "zahtjev za rezervaciju", "ru": "запрос на бронирование"}),
    ActionTypeSpec("staff_question", RequestType.OTHER,
                   {"en": "question for staff", "cnr": "pitanje za osoblje", "ru": "вопрос сотрудникам"}),
]

ACTION_CATALOG: dict[str, ActionTypeSpec] = {s.key: s for s in _SPECS}
_BY_TOPIC: dict[RequestType, str] = {s.topic: s.key for s in _SPECS}


def action_for_topic(topic: RequestType) -> str:
    return _BY_TOPIC.get(topic, "staff_question")


def request_type_for(action_type: str) -> RequestType:
    spec = ACTION_CATALOG.get(action_type)
    return spec.topic if spec else RequestType.OTHER


def action_label(action_type: str, locale: str) -> str:
    spec = ACTION_CATALOG.get(action_type)
    if spec is None:
        return action_type.replace("_", " ")
    if locale == "cnr-Cyrl":
        return latin_to_cyrillic(spec.labels["cnr"])
    return spec.labels.get(locale) or spec.labels.get(locale.split("-")[0]) or spec.labels["en"]


def default_urgency(action_type: str) -> Urgency:
    spec = ACTION_CATALOG.get(action_type)
    return spec.default_urgency if spec else Urgency.NORMAL
