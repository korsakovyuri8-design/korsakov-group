"""Fixed bot utterances per language.

Operational messages (handoffs, request confirmations, emergencies) are
templates, never LLM output: they make commitments on the hotel's behalf
and must be exact. Adding a language = adding a column here.

Montenegrin wording is gender-neutral for the bot ("proslijeđeno je",
not "proslijedio sam").
"""

from __future__ import annotations

from app.agent.language import DEFAULT_LANGUAGE

CATALOG: dict[str, dict[str, str]] = {
    "greeting": {
        "en": "Hello! I'm the virtual assistant of {hotel}. How can I help you?",
        "cnr": "Zdravo! Ja sam virtuelni asistent hotela {hotel}. Kako vam mogu pomoći?",
    },
    "thanks": {
        "en": "You're welcome! Let me know if there is anything else I can help with.",
        "cnr": "Nema na čemu! Javite se ako vam još nešto treba.",
    },
    "goodbye": {
        "en": "Goodbye, and enjoy your stay!",
        "cnr": "Doviđenja i prijatan boravak!",
    },
    "emergency": {
        "en": "This sounds like an emergency. If anyone is in danger, call 112 immediately. "
              "Hotel staff have been alerted and will contact you right away.",
        "cnr": "Ovo zvuči kao hitan slučaj. Ako je nečiji život ili zdravlje u opasnosti, odmah pozovite 112. "
               "Osoblje hotela je obaviješteno i odmah će vas kontaktirati.",
    },
    "handoff_human": {
        "en": "Of course. I'm connecting you with a member of the hotel team; they will reply here as soon as possible.",
        "cnr": "Naravno. Povezujem vas sa članom hotelskog osoblja, koji će vam odgovoriti ovdje u najkraćem roku.",
    },
    "handoff_complaint": {
        "en": "I'm sorry to hear that. I've passed this on to the hotel team and a staff member will get back to you here shortly.",
        "cnr": "Žao nam je zbog toga. Ovo je proslijeđeno hotelskom osoblju i neko od zaposlenih će vam se uskoro javiti ovdje.",
    },
    "handoff_billing": {
        "en": "Thank you for letting us know. Billing questions are handled by our staff, so I've passed this on; "
              "a staff member will get back to you here shortly.",
        "cnr": "Hvala što ste nas obavijestili. Pitanjima u vezi sa računom bavi se naše osoblje, pa je ovo proslijeđeno; "
               "neko od zaposlenih će vam se uskoro javiti ovdje.",
    },
    "handoff_failure": {
        "en": "I'm sorry I couldn't help with this. I've asked a member of the hotel team to take over; they will reply here shortly.",
        "cnr": "Žao nam je što vam ovdje nijesmo mogli pomoći. Razgovor je proslijeđen članu hotelskog tima, koji će vam se uskoro javiti ovdje.",
    },
    "request_created": {
        "en": "I've sent your request to the hotel staff. It is not confirmed yet; staff will confirm it with you here.",
        "cnr": "Vaš zahtjev je proslijeđen osoblju hotela. Zahtjev još nije potvrđen; osoblje će vam ga potvrditi ovdje.",
    },
    "booking_request": {
        "en": "I've passed your booking request to reception, who handle availability and reservations. They will reply to you here.",
        "cnr": "Vaš zahtjev za rezervaciju proslijeđen je recepciji, koja vodi računa o raspoloživosti i rezervacijama. Odgovoriće vam ovdje.",
    },
    "booking_change": {
        "en": "Changes and cancellations have to be handled by our staff, so I've passed your request on. A staff member will reply here shortly.",
        "cnr": "Izmjene i otkazivanja rezervacija rješava naše osoblje, pa je vaš zahtjev proslijeđen. Neko od zaposlenih će vam se uskoro javiti ovdje.",
    },
    "offer_request": {
        "en": "Would you like me to send this request to reception?",
        "cnr": "Želite li da ovaj zahtjev proslijedim recepciji?",
    },
    "cannot_confirm": {
        "en": "I'm sorry, I can't confirm that from the information I have. Would you like me to ask a member of staff?",
        "cnr": "Nažalost, to ne mogu potvrditi na osnovu informacija kojima raspolažem. Želite li da pitam nekoga od osoblja?",
    },
    "question_forwarded": {
        "en": "I've forwarded your question to the hotel staff; they will answer you here.",
        "cnr": "Vaše pitanje je proslijeđeno osoblju hotela; odgovoriće vam ovdje.",
    },
    "offer_declined": {
        "en": "No problem. Is there anything else I can help with?",
        "cnr": "U redu. Mogu li vam još nešto pomoći?",
    },
    "clarify": {
        "en": "Sorry, I didn't quite understand. Could you rephrase? I can help with hotel information, "
              "requests to staff and tips for the area.",
        "cnr": "Izvinite, nije mi sasvim jasno. Možete li preformulisati? Mogu pomoći oko informacija o hotelu, "
               "zahtjeva osoblju i preporuka za okolinu.",
    },
    "unsupported_media": {
        "en": "Sorry, I can only read text messages for now. Please type your question.",
        "cnr": "Izvinite, za sada mogu da čitam samo tekstualne poruke. Molimo vas, napišite pitanje.",
    },
}


def t(key: str, language: str, **params: str) -> str:
    entry = CATALOG[key]
    text = entry.get(language) or entry[DEFAULT_LANGUAGE]
    return text.format(**params) if params else text
