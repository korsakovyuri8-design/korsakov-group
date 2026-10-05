"""Fixed bot utterances per locale.

Operational messages (handoffs, action status, emergencies) are templates,
never LLM output: they make statements about real-world state on the
property's behalf and must be exact (see app/agent/authority.py).

Wording is property-type neutral ("staff", not "hotel staff"). Montenegrin is
gender-neutral for the bot. "cnr-Cyrl" is derived from "cnr" by
transliteration unless a template provides it explicitly. Adding a language
= adding a key to each entry.
"""

from __future__ import annotations

import re

from app.agent.language import DEFAULT_LANGUAGE
from app.text import latin_to_cyrillic

_PLACEHOLDER = re.compile(r"(\{[a-z_]+\})")

CATALOG: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------ small talk
    "greeting": {
        "en": "Hello! I'm the virtual assistant of {property}. How can I help you?",
        "cnr": "Zdravo! Ja sam virtuelni asistent ({property}). Kako vam mogu pomoći?",
        "ru": "Здравствуйте! Я виртуальный ассистент ({property}). Чем могу помочь?",
    },
    "thanks": {
        "en": "You're welcome! Let me know if there is anything else I can help with.",
        "cnr": "Nema na čemu! Javite se ako vam još nešto treba.",
        "ru": "Пожалуйста! Если нужно что-то ещё, напишите.",
    },
    "goodbye": {
        "en": "Goodbye, and enjoy your stay!",
        "cnr": "Doviđenja i prijatan boravak!",
        "ru": "До свидания и приятного отдыха!",
    },
    # ---------------------------------------------------------------- safety
    "emergency": {
        "en": "This sounds like an emergency. If anyone is in danger, call {emergency_number} immediately. "
              "Staff have been alerted and will contact you right away.",
        "cnr": "Ovo zvuči kao hitan slučaj. Ako je nečiji život ili zdravlje u opasnosti, odmah pozovite {emergency_number}. "
               "Osoblje je obaviješteno i odmah će vas kontaktirati.",
        "ru": "Похоже, это экстренная ситуация. Если кому-то угрожает опасность, немедленно звоните {emergency_number}. "
              "Персонал уже оповещён и сейчас же свяжется с вами.",
    },
    "emergency_no_number": {
        "en": "This sounds like an emergency. If anyone is in danger, call the local emergency number immediately. "
              "Staff have been alerted and will contact you right away.",
        "cnr": "Ovo zvuči kao hitan slučaj. Ako je nečiji život ili zdravlje u opasnosti, odmah pozovite lokalni broj za hitne slučajeve. "
               "Osoblje je obaviješteno i odmah će vas kontaktirati.",
        "ru": "Похоже, это экстренная ситуация. Если кому-то угрожает опасность, немедленно звоните в местную экстренную службу. "
              "Персонал уже оповещён и сейчас же свяжется с вами.",
    },
    # --------------------------------------------------------------- handoff
    "handoff_human": {
        "en": "Of course. I'm connecting you with a member of staff; they will reply here as soon as possible.",
        "cnr": "Naravno. Povezujem vas sa članom osoblja, koji će vam odgovoriti ovdje u najkraćem roku.",
        "ru": "Конечно. Я передаю разговор сотруднику, он ответит вам здесь как можно скорее.",
    },
    "handoff_complaint": {
        "en": "I'm sorry to hear that. I've passed this on to the staff and someone will get back to you here shortly.",
        "cnr": "Žao nam je zbog toga. Ovo je proslijeđeno osoblju i neko će vam se uskoro javiti ovdje.",
        "ru": "Нам очень жаль. Это передано сотрудникам, с вами скоро свяжутся здесь.",
    },
    "handoff_billing": {
        "en": "Thank you for letting us know. Billing questions are handled by our staff, so I've passed this on; "
              "someone will get back to you here shortly.",
        "cnr": "Hvala što ste nas obavijestili. Pitanjima u vezi sa računom bavi se naše osoblje, pa je ovo proslijeđeno; "
               "neko će vam se uskoro javiti ovdje.",
        "ru": "Спасибо, что сообщили. Вопросами оплаты занимаются сотрудники, поэтому ваше сообщение передано им; "
              "с вами скоро свяжутся здесь.",
    },
    "handoff_failure": {
        "en": "I'm sorry I couldn't help with this. I've asked a member of staff to take over; they will reply here shortly.",
        "cnr": "Žao nam je što vam ovdje nijesmo mogli pomoći. Razgovor je proslijeđen članu osoblja, koji će vam se uskoro javiti ovdje.",
        "ru": "К сожалению, здесь помочь не получилось. Разговор передан сотруднику, он скоро ответит вам здесь.",
    },
    "booking_change": {
        "en": "Changes and cancellations have to be handled by our staff, so I've passed your request on. Someone will reply here shortly.",
        "cnr": "Izmjene i otkazivanja rezervacija rješava naše osoblje, pa je vaš zahtjev proslijeđen. Neko će vam se uskoro javiti ovdje.",
        "ru": "Изменения и отмены бронирования обрабатывают сотрудники, поэтому ваш запрос передан им. Вам скоро ответят здесь.",
    },
    # ------------------------------------------- action status (authority)
    # One template per ActionStatus. Each may claim exactly its own status and
    # nothing beyond it (enforced by tests/test_authority.py).
    "action_submitted": {
        "en": "I've sent your request to the staff. It is not confirmed yet; they will confirm it with you here.",
        "cnr": "Vaš zahtjev je proslijeđen osoblju. Zahtjev još nije potvrđen; osoblje će vam ga potvrditi ovdje.",
        "ru": "Ваш запрос передан сотрудникам. Он ещё не подтверждён; вам ответят здесь.",
    },
    "action_submitted_named": {
        "en": "I've sent your request ({action}) to the staff. It is not confirmed yet; they will confirm it with you here.",
        "cnr": "Vaš zahtjev ({action}) je proslijeđen osoblju. Zahtjev još nije potvrđen; osoblje će vam ga potvrditi ovdje.",
        "ru": "Ваш запрос ({action}) передан сотрудникам. Он ещё не подтверждён; вам ответят здесь.",
    },
    "action_accepted": {
        "en": "Your request ({action}) has been accepted by {property}.",
        "cnr": "Vaš zahtjev ({action}) je prihvaćen ({property}).",
        "ru": "Ваш запрос ({action}) принят ({property}).",
    },
    "action_in_progress": {
        "en": "Your request ({action}) is being handled.",
        "cnr": "Vaš zahtjev ({action}) je u obradi.",
        "ru": "Ваш запрос ({action}) сейчас выполняется.",
    },
    "action_completed": {
        "en": "Your request ({action}) has been completed.",
        "cnr": "Vaš zahtjev ({action}) je izvršen.",
        "ru": "Ваш запрос ({action}) выполнен.",
    },
    "action_rejected": {
        "en": "Unfortunately, your request ({action}) could not be accepted.",
        "cnr": "Nažalost, vaš zahtjev ({action}) nije mogao biti prihvaćen.",
        "ru": "К сожалению, ваш запрос ({action}) не может быть принят.",
    },
    "action_failed": {
        "en": "I couldn't submit your request ({action}) automatically.",
        "cnr": "Vaš zahtjev ({action}) nije bilo moguće automatski poslati.",
        "ru": "Не удалось автоматически отправить ваш запрос ({action}).",
    },
    "action_failed_fallback": {
        "en": "I couldn't submit your request ({action}) automatically, so I've sent it to the staff instead. "
              "It is not confirmed yet; they will confirm it with you here.",
        "cnr": "Vaš zahtjev ({action}) nije bilo moguće automatski poslati, pa je proslijeđen osoblju. "
               "Zahtjev još nije potvrđen; osoblje će vam ga potvrditi ovdje.",
        "ru": "Не удалось автоматически отправить ваш запрос ({action}), поэтому он передан сотрудникам. "
              "Он ещё не подтверждён; вам ответят здесь.",
    },
    "action_cancelled": {
        "en": "Your request ({action}) has been cancelled.",
        "cnr": "Vaš zahtjev ({action}) je otkazan.",
        "ru": "Ваш запрос ({action}) отменён.",
    },
    "no_actions_yet": {
        "en": "I don't see any request from you yet. What would you like me to send to the staff?",
        "cnr": "Za sada ne vidim nijedan vaš zahtjev. Šta želite da proslijedim osoblju?",
        "ru": "Я пока не вижу ваших запросов. Что передать сотрудникам?",
    },
    "capability_unavailable": {
        "en": "I'm sorry, I can't arrange that for you through this chat ({action} is not available here). "
              "Would you like me to ask a member of staff?",
        "cnr": "Nažalost, preko ovog razgovora ne mogu da organizujem: {action}. Želite li da pitam nekoga od osoblja?",
        "ru": "К сожалению, через этот чат я не могу организовать: {action}. Хотите, я спрошу сотрудников?",
    },
    "booking_request": {
        "en": "I've passed your booking request to the staff, who handle availability and reservations. "
              "Nothing is booked yet; they will reply to you here.",
        "cnr": "Vaš zahtjev za rezervaciju proslijeđen je osoblju, koje vodi računa o raspoloživosti i rezervacijama. "
               "Ništa još nije rezervisano; odgovoriće vam ovdje.",
        "ru": "Ваш запрос на бронирование передан сотрудникам, которые отвечают за наличие мест и бронирования. "
              "Пока ничего не забронировано; вам ответят здесь.",
    },
    # ------------------------------------------------------ dialogue/fallback
    "offer_request": {
        "en": "Would you like me to send this request to the staff?",
        "cnr": "Želite li da ovaj zahtjev proslijedim osoblju?",
        "ru": "Хотите, я передам этот запрос сотрудникам?",
    },
    "cannot_confirm": {
        "en": "I'm sorry, I can't confirm that from the information I have. Would you like me to ask a member of staff?",
        "cnr": "Nažalost, to ne mogu potvrditi na osnovu informacija kojima raspolažem. Želite li da pitam nekoga od osoblja?",
        "ru": "К сожалению, я не могу это подтвердить по имеющейся у меня информации. Хотите, я спрошу сотрудников?",
    },
    "partial_unknown": {
        "en": "I can't confirm the rest of your question from the information I have.",
        "cnr": "Ostatak vašeg pitanja ne mogu potvrditi na osnovu informacija kojima raspolažem.",
        "ru": "Остальную часть вопроса я не могу подтвердить по имеющейся информации.",
    },
    "knowledge_conflict": {
        "en": "I have conflicting information about this, so I'd rather not guess. Would you like me to ask a member of staff?",
        "cnr": "O ovome imam oprečne informacije, pa ne bih da nagađam. Želite li da pitam nekoga od osoblja?",
        "ru": "У меня противоречивая информация по этому вопросу, и я не хочу гадать. Хотите, я спрошу сотрудников?",
    },
    "knowledge_language_fallback": {
        "en": "{text}",
        "cnr": "{text}",
        "ru": "Эта информация доступна мне только на английском: {text}",
    },
    "facts_noted": {
        "en": "Thank you, I've noted: {facts}. This is for our staff's information; it does not change or confirm a booking.",
        "cnr": "Hvala, zabilježeno je: {facts}. Ovo je informacija za naše osoblje i ne mijenja niti potvrđuje rezervaciju.",
        "ru": "Спасибо, отмечено: {facts}. Это информация для персонала; она не меняет и не подтверждает бронирование.",
    },
    "question_forwarded": {
        "en": "I've forwarded your question to the staff; they will answer you here.",
        "cnr": "Vaše pitanje je proslijeđeno osoblju; odgovoriće vam ovdje.",
        "ru": "Ваш вопрос передан сотрудникам; вам ответят здесь.",
    },
    "offer_declined": {
        "en": "No problem. Is there anything else I can help with?",
        "cnr": "U redu. Mogu li vam još nešto pomoći?",
        "ru": "Хорошо. Могу ли я ещё чем-то помочь?",
    },
    "clarify": {
        "en": "Sorry, I didn't quite understand. Could you rephrase? I can help with information about the property, "
              "requests to staff and tips for the area.",
        "cnr": "Izvinite, nije mi sasvim jasno. Možete li preformulisati? Mogu pomoći oko informacija o smještaju, "
               "zahtjeva osoblju i preporuka za okolinu.",
        "ru": "Извините, не совсем понятно. Можете переформулировать? Я могу помочь с информацией о месте проживания, "
              "запросами к персоналу и советами по окрестностям.",
    },
    "unsupported_media": {
        "en": "Sorry, I can only read text messages for now. Please type your question.",
        "cnr": "Izvinite, za sada mogu da čitam samo tekstualne poruke. Molimo vas, napišite pitanje.",
        "ru": "Извините, пока я понимаю только текстовые сообщения. Пожалуйста, напишите вопрос.",
    },
}

# Back-compat key from Core v1.
CATALOG["request_created"] = CATALOG["action_submitted"]


def t(key: str, locale: str, **params: str) -> str:
    entry = CATALOG[key]
    if locale == "cnr-Cyrl" and "cnr-Cyrl" not in entry:
        template = to_cyrillic_template(entry["cnr"])
    else:
        template = entry.get(locale) or entry.get(locale.split("-")[0]) or entry[DEFAULT_LANGUAGE]
    return template.format(**params) if params else template


def to_cyrillic_template(template: str) -> str:
    """Transliterate a Latin template, leaving {placeholders} intact."""
    return "".join(
        part if _PLACEHOLDER.fullmatch(part) else latin_to_cyrillic(part) for part in _PLACEHOLDER.split(template)
    )


FACT_LABELS: dict[str, dict[str, str]] = {
    "guest_count": {"en": "number of guests", "cnr": "broj gostiju", "ru": "количество гостей"},
    "arrival_date": {"en": "arrival", "cnr": "dolazak", "ru": "заезд"},
    "departure_date": {"en": "departure", "cnr": "odlazak", "ru": "выезд"},
    "room_number": {"en": "room", "cnr": "soba", "ru": "номер"},
}


def describe_facts(facts: dict[str, object], locale: str) -> str:
    """'number of guests: 2; arrival: 2026-12-20' in the guest's locale
    (label: value avoids grammatical agreement problems)."""
    base = locale.split("-")[0]
    parts = []
    for key, value in facts.items():
        label = FACT_LABELS.get(key, {}).get(base) or FACT_LABELS.get(key, {}).get("en") or key
        if key.endswith("_date") and base in ("cnr", "ru") and isinstance(value, str) and len(value) == 10:
            y, m, d = value.split("-")
            value = f"{d}.{m}.{y}" + ("." if base == "cnr" else "")
        parts.append(f"{label}: {value}")
    text = "; ".join(parts)
    return latin_to_cyrillic(text) if locale == "cnr-Cyrl" else text
