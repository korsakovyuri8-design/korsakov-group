"""Catalogue of external (third-party) service types.

A service type is what a guest can *buy or reserve* from an external
provider. Each declares its provider domain and the details needed to quote
it, as typed fields; extraction works per field *type* (datetime, count,
place, text), so most services need no custom code. Transport is the first
reference implementation; the rest are declared so packs can enable them
once a provider adapter exists.

Every service here creates an externally binding commitment, so every one
requires a quote and explicit guest consent before submission.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import RequestType
from app.text import latin_to_cyrillic

PROVIDER_TYPES = ("transport", "restaurant", "activities", "ski_rental", "car_rental", "spa", "tickets",
                  "food_delivery", "other")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    kind: str                    # datetime | count | place | text
    labels: dict[str, str]
    required: bool = True


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    domain: str                  # provider_type that can fulfil it
    labels: dict[str, str]
    fields: tuple[FieldSpec, ...]


_WHEN = {"en": "time", "cnr": "vrijeme", "ru": "время"}
_PEOPLE = {"en": "passengers", "cnr": "putnika", "ru": "пассажиров"}
_GUESTS = {"en": "guests", "cnr": "osoba", "ru": "гостей"}
_TRANSPORT_FIELDS = (
    FieldSpec("pickup_time", "datetime", _WHEN),
    FieldSpec("pickup", "place", {"en": "pickup", "cnr": "polazište", "ru": "откуда"}),
    FieldSpec("destination", "place", {"en": "destination", "cnr": "odredište", "ru": "куда"}),
    FieldSpec("party_size", "count", _PEOPLE),
)

_SPECS = [
    ServiceSpec("airport_transfer", "transport",
                {"en": "airport transfer", "cnr": "transfer do aerodroma", "ru": "трансфер в аэропорт"},
                _TRANSPORT_FIELDS),
    ServiceSpec("taxi", "transport", {"en": "taxi", "cnr": "taksi", "ru": "такси"}, _TRANSPORT_FIELDS),
    ServiceSpec("restaurant_reservation", "restaurant",
                {"en": "restaurant reservation", "cnr": "rezervacija restorana", "ru": "бронирование ресторана"},
                (FieldSpec("reservation_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS))),
    ServiceSpec("activity_booking", "activities", {"en": "activity", "cnr": "aktivnost", "ru": "экскурсия"},
                (FieldSpec("activity", "text", {"en": "activity", "cnr": "aktivnost", "ru": "активность"}),
                 FieldSpec("start_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS))),
    ServiceSpec("ski_rental", "ski_rental", {"en": "ski rental", "cnr": "najam skija", "ru": "прокат лыж"},
                (FieldSpec("start_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS))),
    ServiceSpec("car_rental", "car_rental", {"en": "car rental", "cnr": "najam automobila", "ru": "аренда автомобиля"},
                (FieldSpec("start_time", "datetime", _WHEN),)),
    ServiceSpec("spa_booking", "spa", {"en": "spa booking", "cnr": "spa termin", "ru": "запись в спа"},
                (FieldSpec("start_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS))),
    ServiceSpec("event_tickets", "tickets", {"en": "event tickets", "cnr": "ulaznice", "ru": "билеты"},
                (FieldSpec("event", "text", {"en": "event", "cnr": "događaj", "ru": "мероприятие"}),
                 FieldSpec("party_size", "count", _GUESTS))),
    ServiceSpec("food_delivery", "food_delivery", {"en": "food delivery", "cnr": "dostava hrane", "ru": "доставка еды"},
                (FieldSpec("order", "text", {"en": "order", "cnr": "narudžba", "ru": "заказ"}),
                 FieldSpec("delivery_time", "datetime", _WHEN))),
]

SERVICE_CATALOG: dict[str, ServiceSpec] = {s.key: s for s in _SPECS}

# Request topics recognised by the intent layer -> candidate service types.
TOPIC_SERVICES: dict[RequestType, tuple[str, ...]] = {
    RequestType.TRANSPORT: ("airport_transfer", "taxi"),
    RequestType.RESTAURANT: ("restaurant_reservation",),
}


def localized(labels: dict[str, str], locale: str) -> str:
    if locale == "cnr-Cyrl":
        return latin_to_cyrillic(labels.get("cnr") or labels["en"])
    return labels.get(locale) or labels.get(locale.split("-")[0]) or labels["en"]


def service_label(service_type: str, locale: str) -> str:
    spec = SERVICE_CATALOG.get(service_type)
    return localized(spec.labels, locale) if spec else service_type.replace("_", " ")
