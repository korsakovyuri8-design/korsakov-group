"""Material terms of an offering, shown with the price BEFORE consent.

Provider policies are defaults; an offering's own policies override them.
The quote stores the exact terms shown, and consent to the quote is
recorded together with them. No deposit or payment is processed here -
terms only state what the provider will require.
"""

from __future__ import annotations

from typing import Any

from app.agent import messages as msg

# Keys a guest must see before agreeing (order = display order).
MATERIAL = ("weather_dependent", "deposit", "damage_deposit", "id_required", "min_age", "driving_licence",
            "min_duration_hours", "pickup_hours", "return_rules", "late_return_fee", "meeting_point",
            "duration_hours", "difficulty", "required_equipment", "included", "excluded",
            "free_cancellation_hours", "cancellation_fee")

_L: dict[str, dict[str, str]] = {
    "weather_dependent": {"en": "depends on weather - the provider confirms on the day before",
                          "cnr": "zavisi od vremena - partner potvrđuje dan ranije",
                          "ru": "зависит от погоды — партнёр подтвердит накануне"},
    "deposit": {"en": "deposit {v} paid to the provider at pickup", "cnr": "depozit {v} plaća se partneru pri preuzimanju",
                "ru": "залог {v} оплачивается партнёру при получении"},
    "damage_deposit": {"en": "damage deposit {v}", "cnr": "depozit za štetu {v}", "ru": "залог за повреждения {v}"},
    "id_required": {"en": "ID/passport required", "cnr": "potreban lični dokument/pasoš", "ru": "нужен паспорт/удостоверение"},
    "min_age": {"en": "minimum age {v}", "cnr": "minimalni uzrast {v}", "ru": "минимальный возраст {v}"},
    "driving_licence": {"en": "driving licence required ({v})", "cnr": "potrebna vozačka dozvola ({v})",
                        "ru": "нужны водительские права ({v})"},
    "min_duration_hours": {"en": "minimum duration {v} h", "cnr": "minimalno trajanje {v} h", "ru": "минимум {v} ч"},
    "pickup_hours": {"en": "pickup {v}", "cnr": "preuzimanje {v}", "ru": "выдача {v}"},
    "return_rules": {"en": "return: {v}", "cnr": "vraćanje: {v}", "ru": "возврат: {v}"},
    "late_return_fee": {"en": "late return {v}", "cnr": "kašnjenje s vraćanjem {v}", "ru": "поздний возврат {v}"},
    "meeting_point": {"en": "meeting point: {v}", "cnr": "mjesto sastanka: {v}", "ru": "место встречи: {v}"},
    "duration_hours": {"en": "duration {v} h", "cnr": "trajanje {v} h", "ru": "длительность {v} ч"},
    "difficulty": {"en": "difficulty: {v}", "cnr": "težina: {v}", "ru": "сложность: {v}"},
    "required_equipment": {"en": "bring: {v}", "cnr": "ponesite: {v}", "ru": "взять с собой: {v}"},
    "included": {"en": "included: {v}", "cnr": "uključeno: {v}", "ru": "включено: {v}"},
    "excluded": {"en": "not included: {v}", "cnr": "nije uključeno: {v}", "ru": "не включено: {v}"},
    "free_cancellation_hours": {"en": "free cancellation up to {v} h before", "cnr": "besplatno otkazivanje do {v} h prije",
                                "ru": "бесплатная отмена за {v} ч"},
    "cancellation_fee": {"en": "later cancellation: {v}", "cnr": "kasnije otkazivanje: {v}", "ru": "более поздняя отмена: {v}"},
}
_HEADER = {"en": "Important terms", "cnr": "Važni uslovi", "ru": "Важные условия"}


def merged(provider_policies: dict[str, Any] | None, offering_policies: dict[str, Any] | None,
           offering_attrs: dict[str, Any] | None = None) -> dict[str, Any]:
    terms = {**(provider_policies or {}), **(offering_policies or {})}
    attrs = offering_attrs or {}
    for key in ("weather_dependent", "meeting_point", "duration_hours", "difficulty", "required_equipment",
                "included", "excluded", "min_age"):
        if key in attrs and key not in terms:
            terms[key] = attrs[key]
    return {k: terms[k] for k in MATERIAL if terms.get(k) not in (None, False, "", [])}


def _value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):   # e.g. {"amount": 200, "currency": "EUR"}
        return f"{v.get('amount')} {v.get('currency', '')}".strip()
    return str(v)


def render(terms: dict[str, Any], locale: str) -> str:
    if not terms:
        return ""
    base = locale.split("-")[0]
    parts = []
    for key in MATERIAL:
        if key not in terms:
            continue
        entry = _L[key]
        text = (entry.get(base) or entry["en"]).format(v=_value(terms[key]))
        parts.append(msg.to_cyrillic_template(text) if locale == "cnr-Cyrl" else text)
    header = _HEADER.get(base) or _HEADER["en"]
    if locale == "cnr-Cyrl":
        header = msg.to_cyrillic_template(header)
    return f"{header}: " + "; ".join(parts) + ". "
