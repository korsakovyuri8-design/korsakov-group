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

import re
from dataclasses import dataclass

from app.db.models import RequestType
from app.text import latin_to_cyrillic

PROVIDER_TYPES = ("transport", "restaurant", "activities", "rental", "spa", "tickets", "food_delivery", "guide",
                  "nightlife", "other")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    # datetime | count (people) | quantity | luggage | child_seats | hours | days | place | text | language |
    # place_ref | rental_category | heights | skill | activity | format
    kind: str
    labels: dict[str, str]
    required: bool = True


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    domain: str                  # provider_type that can fulfil it
    labels: dict[str, str]
    fields: tuple[FieldSpec, ...]
    # Phrases (folded matching, "*" = prefix) that ask for this service.
    keywords: tuple[str, ...] = ()


_WHEN = {"en": "time", "cnr": "vrijeme", "ru": "время"}
_PEOPLE = {"en": "passengers", "cnr": "putnika", "ru": "пассажиров"}
_GUESTS = {"en": "guests", "cnr": "osoba", "ru": "гостей"}
_TRANSPORT_FIELDS = (
    FieldSpec("pickup_time", "datetime", _WHEN),
    FieldSpec("pickup", "place", {"en": "pickup", "cnr": "polazište", "ru": "откуда"}),
    FieldSpec("destination", "place", {"en": "destination", "cnr": "odredište", "ru": "куда"}),
    FieldSpec("party_size", "count", _PEOPLE),
    # Optional unless the chosen offering requires them.
    FieldSpec("luggage", "luggage", {"en": "luggage", "cnr": "prtljag", "ru": "багаж"}, required=False),
    FieldSpec("child_seats", "child_seats", {"en": "child seats", "cnr": "dječja sjedišta", "ru": "детские кресла"},
              required=False),
    FieldSpec("vehicle", "text", {"en": "vehicle", "cnr": "vozilo", "ru": "автомобиль"}, required=False),
)
_RENTAL_FIELDS = (
    FieldSpec("category", "rental_category", {"en": "item", "cnr": "oprema", "ru": "что"}),
    FieldSpec("quantity", "quantity", {"en": "quantity", "cnr": "količina", "ru": "количество"}),
    FieldSpec("start_time", "datetime", {"en": "from", "cnr": "od", "ru": "с"}),
    FieldSpec("days", "days", {"en": "days", "cnr": "dana", "ru": "дней"}, required=False),
    FieldSpec("hours", "hours", {"en": "hours", "cnr": "sati", "ru": "часов"}, required=False),
    FieldSpec("heights", "heights", {"en": "heights", "cnr": "visine", "ru": "рост"}, required=False),
    FieldSpec("skill_level", "skill", {"en": "level", "cnr": "nivo", "ru": "уровень"}, required=False),
    FieldSpec("driver_age", "age", {"en": "driver's age", "cnr": "godine vozača", "ru": "возраст водителя"},
              required=False),
    FieldSpec("delivery", "text", {"en": "delivery", "cnr": "dostava", "ru": "доставка"}, required=False),
)
_GUIDE_FIELDS = (
    FieldSpec("start_time", "datetime", _WHEN),
    FieldSpec("party_size", "count", _GUESTS),
    FieldSpec("language", "language", {"en": "language", "cnr": "jezik", "ru": "язык"}, required=False),
    FieldSpec("activity", "activity", {"en": "activity", "cnr": "aktivnost", "ru": "тип тура"}, required=False),
    FieldSpec("format", "format", {"en": "format", "cnr": "format", "ru": "формат"}, required=False),
    FieldSpec("hours", "hours", {"en": "hours", "cnr": "sati", "ru": "часов"}, required=False),
)

_SPECS = [
    ServiceSpec("airport_transfer", "transport",
                {"en": "airport transfer", "cnr": "transfer do aerodroma", "ru": "трансфер в аэропорт"},
                _TRANSPORT_FIELDS,
                keywords=("airport transfer", "transfer", "shuttle", "pick us up", "pick me up", "transfer do aerodrom*", "transfer*", "трансфер*")),
    ServiceSpec("intercity_transfer", "transport",
                {"en": "intercity transfer", "cnr": "međugradski prevoz", "ru": "междугородний трансфер"},
                _TRANSPORT_FIELDS,
                keywords=("intercity", "medjugradsk*", "междугородн*")),
    ServiceSpec("taxi", "transport", {"en": "taxi", "cnr": "taksi", "ru": "такси"}, _TRANSPORT_FIELDS,
                keywords=("taxi", "cab", "ride to", "taksi", "такси")),
    ServiceSpec("restaurant_reservation", "restaurant",
                {"en": "table reservation", "cnr": "rezervacija stola", "ru": "бронирование столика"},
                (FieldSpec("venue", "place_ref", {"en": "venue", "cnr": "mjesto", "ru": "заведение"}),
                 FieldSpec("reservation_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS)),
                keywords=("table for", "book a table", "reserve a table", "table at", "sto za", "stol za", "rezervis* sto", "столик*")),
    ServiceSpec("bar_table", "nightlife",
                {"en": "bar table", "cnr": "rezervacija stola u baru", "ru": "столик в баре"},
                (FieldSpec("venue", "place_ref", {"en": "venue", "cnr": "mjesto", "ru": "заведение"}),
                 FieldSpec("reservation_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS)),
                keywords=("table at the bar", "bar table", "sto u baru", "столик в бар*")),
    ServiceSpec("guide_booking", "guide", {"en": "guide", "cnr": "vodič", "ru": "гид"}, _GUIDE_FIELDS,
                keywords=("guide", "tour guide", "guided tour", "guided hike", "city tour", "walking tour", "private tour",
                          "group tour", "excursion", "vodic*", "tura sa vodic*", "ekskurzij*", "гид*", "экскурсовод*",
                          "экскурси*")),
    ServiceSpec("activity_booking", "activities", {"en": "activity", "cnr": "aktivnost", "ru": "экскурсия"},
                (FieldSpec("activity", "text", {"en": "activity", "cnr": "aktivnost", "ru": "активность"}),
                 FieldSpec("start_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS))),
    # One generic rental service: the item category is a field, and the
    # offering decides which further details it needs (heights, days, age...).
    ServiceSpec("rental", "rental", {"en": "rental", "cnr": "najam", "ru": "прокат"}, _RENTAL_FIELDS,
                keywords=("rent", "rental", "hire", "skis", "ski rental", "ski set*", "ski equipment", "sets of skis",
                          "pairs of skis", "snowboard*", "bike", "bikes", "bicycle*", "e-bike*", "ebike*",
                          "car rental", "rent a car", "hire a car", "scooter*", "motorbike*", "motorcycle*", "tent*",
                          "hiking equipment", "hiking gear", "camping equipment", "camping gear",
                          "najam", "iznajm*", "skije", "skija", "bicikl*", "rent-a-car", "skuter*", "motor",
                          "прокат*", "аренд*", "лыж*", "сноуборд*", "велосипед*", "электровелосипед*", "палатк*",
                          "машин* напрокат", "скутер*")),
    ServiceSpec("spa_booking", "spa", {"en": "spa booking", "cnr": "spa termin", "ru": "запись в спа"},
                (FieldSpec("start_time", "datetime", _WHEN), FieldSpec("party_size", "count", _GUESTS))),
    ServiceSpec("event_tickets", "tickets", {"en": "event tickets", "cnr": "ulaznice", "ru": "билеты"},
                (FieldSpec("event", "text", {"en": "event", "cnr": "događaj", "ru": "мероприятие"}),
                 FieldSpec("party_size", "count", _GUESTS))),
    ServiceSpec("food_delivery", "food_delivery", {"en": "food delivery", "cnr": "dostava hrane", "ru": "доставка еды"},
                (FieldSpec("order", "text", {"en": "order", "cnr": "narudžba", "ru": "заказ"}),
                 FieldSpec("delivery_time", "datetime", _WHEN))),
]

# Rental item categories: data, not schema. Adding one = one line here (labels
# + words guests use) and offerings that list it in attributes.categories.
RENTAL_CATEGORIES: dict[str, tuple[dict[str, str], tuple[str, ...]]] = {
    "ski": ({"en": "skis", "cnr": "skije", "ru": "лыжи"},
            ("ski", "skis", "skije", "skija", "ski oprem*", "ski set*", "лыж*")),
    "snowboard": ({"en": "snowboard", "cnr": "snoubord", "ru": "сноуборд"},
                  ("snowboard*", "snoubord*", "сноуборд*")),
    "bicycle": ({"en": "bicycle", "cnr": "bicikl", "ru": "велосипед"},
                ("bike", "bikes", "bicycle*", "mountain bike*", "bicikl*", "велосипед*")),
    "e_bike": ({"en": "e-bike", "cnr": "električni bicikl", "ru": "электровелосипед"},
               ("e-bike*", "ebike*", "e bike*", "electric bike*", "elektricn* bicikl*", "электровелосипед*")),
    "car": ({"en": "car", "cnr": "automobil", "ru": "автомобиль"},
            ("car", "a car", "rent-a-car", "automobil*", "auto", "автомобил*", "машин*")),
    "scooter": ({"en": "scooter / motorbike", "cnr": "skuter / motor", "ru": "скутер / мотоцикл"},
                ("scooter*", "motorbike*", "motorcycle*", "moped*", "skuter*", "motor", "скутер*", "мотоцикл*")),
    "hiking_equipment": ({"en": "hiking equipment", "cnr": "planinarska oprema", "ru": "снаряжение для хайкинга"},
                         ("hiking equipment", "hiking gear", "trekking poles", "planinarsk* oprem*",
                          "треккинговые палки", "снаряжение для поход*")),
    "camping_equipment": ({"en": "camping equipment", "cnr": "oprema za kampovanje", "ru": "снаряжение для кемпинга"},
                          ("camping equipment", "camping gear", "tent", "tents", "sleeping bag*", "sator*",
                           "oprem* za kamp*", "палатк*", "спальн* мешк*", "снаряжение для кемпинг*")),
}
# e-bike before bicycle and specific before generic when matching.
RENTAL_MATCH_ORDER = ("e_bike", "snowboard", "ski", "car", "scooter", "camping_equipment", "hiking_equipment",
                      "bicycle")

# Destinations far enough from the demo region to be intercity trips (a real
# deployment would derive this from distance; kept as data, not logic).
INTERCITY_PLACES = ["kotor", "budva", "herceg novi", "bar", "ulcinj", "tivat", "niksic", "pljevlja", "cetinje",
                    "podgorica", "belgrade", "beograd", "dubrovnik", "sarajevo", "kolasin", "котор", "будв*",
                    "подгориц*", "белград*", "дубровник*"]

GUIDE_ACTIVITIES: dict[str, tuple[str, ...]] = {
    "hiking": ("hike", "hiking", "trek*", "planinar*", "pjesac*", "поход*", "хайкинг*", "треккинг*"),
    "mountain": ("mountain", "summit", "peak", "bobotov*", "planin*", "vrh", "горн*", "гор", "вершин*"),
    "durmitor": ("durmitor", "дурмитор*", "black lake", "crno jezero", "черное озеро"),
    "canyon": ("canyon", "tara", "kanjon*", "каньон*"),
    "cultural": ("cultural", "culture", "city tour", "town tour", "history", "historic*", "museum*", "kultur*",
                 "istorij*", "культур*", "истори*", "город*"),
    "nature": ("nature", "wildlife", "flora", "priroda", "природ*"),
    "adventure": ("adventure", "rafting", "climb*", "avantur*", "приключен*", "рафтинг*"),
}
SERVICE_CATALOG: dict[str, ServiceSpec] = {s.key: s for s in _SPECS}

# Request topics recognised by the intent layer -> candidate service types.
TOPIC_SERVICES: dict[RequestType, tuple[str, ...]] = {
    RequestType.TRANSPORT: ("airport_transfer", "taxi", "intercity_transfer"),
    RequestType.RESTAURANT: ("restaurant_reservation",),
}

# Fields of kind place_ref may only reference places of these categories.
VENUE_CATEGORIES = {"restaurant_reservation": ("FOOD",), "bar_table": ("NIGHTLIFE",)}


def localized(labels: dict[str, str], locale: str) -> str:
    if locale == "cnr-Cyrl":
        return latin_to_cyrillic(labels.get("cnr") or labels["en"])
    return labels.get(locale) or labels.get(locale.split("-")[0]) or labels["en"]


def service_label(service_type: str, locale: str) -> str:
    spec = SERVICE_CATALOG.get(service_type)
    return localized(spec.labels, locale) if spec else service_type.replace("_", " ")


def detect_services(text: str) -> list[str]:
    """Service types the message asks for, by keyword (catalogue order)."""
    from app.text import contains_phrase, fold

    folded = fold(text)
    found = [spec.key for spec in _SPECS if any(contains_phrase(folded, k) for k in spec.keywords)]
    if not any(s in found for s in ("airport_transfer", "taxi", "intercity_transfer")) and _GET_TO.search(folded) \
            and _NEED.search(folded):
        # "We need to get to Podgorica airport at 6" is a transport request too.
        found.insert(0, "airport_transfer" if _AIRPORT_WORD.search(folded) else "taxi")
    if "rental" in found and not rental_category(folded) and not any(
            contains_phrase(folded, k) for k in ("rent", "rental", "hire", "najam", "iznajm*", "прокат*", "аренд*")):
        found.remove("rental")   # "car" alone is not a rental request
    return found


_AIRPORT_WORD = re.compile(r"airport|aerodrom|aeroport|аэропорт|аеродром")
_GET_TO = re.compile(r"\b(get|go|drive|ride|bring|take)\s+(us|me|them)?\s*(to|from)\b|\bneed (a lift|a ride)\b|"
                     r"\bpick (us|me) up\b|\b(stici|doci|otici|ici)\s+(do|na|u)\b|\b(prevoz|prijevoz)\b|"
                     r"\b(dobrat\w*|doehat\w*|dovezti|otvezti|podvezti|zabrat nas)\b")
# ...and only when it is a request, not "How do I get to the lake?"
_NEED = re.compile(r"\b(need|needs|want|have to|must|book|arrange|organi[sz]e|can you|could you|please|treba|"
                   r"moramo|moram|zelimo|hocemo|nuzn\w*|nado|hotim|hochu|zakaz\w*|organizuj\w*)\b")


def rental_category(folded: str) -> str | None:
    from app.text import contains_phrase

    for key in RENTAL_MATCH_ORDER:
        if any(contains_phrase(folded, k) for k in RENTAL_CATEGORIES[key][1]):
            return key
    return None


def rental_label(category: str, locale: str) -> str:
    entry = RENTAL_CATEGORIES.get(category)
    return localized(entry[0], locale) if entry else category.replace("_", " ")


def keyword_positions(folded: str) -> list[tuple[int, str]]:
    """(position, service_type) of the first keyword hit of each service -
    used to split one sentence that asks for several services."""
    from app.text import fold as _fold

    out = []
    for spec in _SPECS:
        best = None
        for k in spec.keywords:
            stem = _fold(k).rstrip("*")
            m = re.search(rf"\b{re.escape(stem)}", folded)
            if m and (best is None or m.start() < best):
                best = m.start()
        if best is not None:
            out.append((best, spec.key))
    return sorted(out)
