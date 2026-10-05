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
    # ------------------------------------------- external transactions
    # A quote is never a booking. Every state has its own template; nothing
    # here may claim more than the stored state (tests/test_authority.py).
    "quote_offer": {
        "en": "{provider} can do this for {price}: {summary}. {conditions}The offer is valid until {valid_until}. "
              "Nothing is booked yet - reply \"yes\" to book it (offer {code}).",
        "cnr": "{provider} može ovo da obavi za {price}: {summary}. {conditions}Ponuda važi do {valid_until}. "
               "Ništa još nije rezervisano - odgovorite \"da\" da biste rezervisali (ponuda {code}).",
        "ru": "{provider} может выполнить это за {price}: {summary}. {conditions}Предложение действует до {valid_until}. "
              "Пока ничего не забронировано — ответьте «да», чтобы забронировать (предложение {code}).",
    },
    "quote_updated": {
        "en": "Updated offer: ", "cnr": "Nova ponuda: ", "ru": "Новое предложение: ",
    },
    "quote_expired": {
        "en": "That offer has expired, so nothing was booked. ",
        "cnr": "Ta ponuda je istekla, pa ništa nije rezervisano. ",
        "ru": "Срок действия того предложения истёк, поэтому ничего не забронировано. ",
    },
    "quote_clarify": {
        "en": "To book, I need a clear \"yes\" for offer {code} ({price}: {summary}). {provider} sets the price. "
              "Reply \"no\" if you don't want it, or tell me what to change. Nothing is booked yet.",
        "cnr": "Za rezervaciju mi je potrebno jasno \"da\" za ponudu {code} ({price}: {summary}). Cijenu određuje {provider}. "
               "Odgovorite \"ne\" ako je ne želite ili mi recite šta da promijenim. Ništa još nije rezervisano.",
        "ru": "Для бронирования мне нужно чёткое «да» на предложение {code} ({price}: {summary}). Цену устанавливает {provider}. "
              "Ответьте «нет», если оно вам не нужно, или скажите, что изменить. Пока ничего не забронировано.",
    },
    "quote_declined": {
        "en": "No problem, nothing has been booked.",
        "cnr": "U redu, ništa nije rezervisano.",
        "ru": "Хорошо, ничего не забронировано.",
    },
    "quote_unavailable": {
        "en": "I couldn't get a price from {provider} right now, so nothing has been booked. I've passed your request "
              "to the staff; they will reply here.",
        "cnr": "Trenutno ne mogu da dobijem cijenu od: {provider}, pa ništa nije rezervisano. Vaš zahtjev je proslijeđen "
               "osoblju; odgovoriće vam ovdje.",
        "ru": "Сейчас не удаётся получить цену от {provider}, поэтому ничего не забронировано. Ваш запрос передан "
              "сотрудникам; вам ответят здесь.",
    },
    "txn_draft_dropped": {
        "en": "OK, I've dropped that request. Nothing was booked.",
        "cnr": "U redu, odustali smo od tog zahtjeva. Ništa nije rezervisano.",
        "ru": "Хорошо, этот запрос отменён. Ничего не забронировано.",
    },
    "txn_sending": {
        "en": "Thank you. I'm sending your booking to {provider} now. It is not confirmed yet; I'll tell you as soon as "
              "they reply.",
        "cnr": "Hvala. Vaša rezervacija se šalje: {provider}. Još nije potvrđena; javiću vam čim odgovore.",
        "ru": "Спасибо. Ваше бронирование отправляется в {provider}. Оно ещё не подтверждено; я сообщу, как только придёт ответ.",
    },
    "txn_submitted": {
        "en": "{provider} has received your booking request ({service}). It is not confirmed yet; I'll tell you as soon "
              "as they confirm.",
        "cnr": "{provider} je primio vaš zahtjev za rezervaciju ({service}). Još nije potvrđen; javiću vam čim ga potvrde.",
        "ru": "{provider} получил ваш запрос на бронирование ({service}). Он ещё не подтверждён; я сообщу, как только его подтвердят.",
    },
    "txn_accepted": {
        "en": "{provider} has accepted your booking ({service}: {summary}). Reference: {reference}.",
        "cnr": "{provider} je prihvatio vašu rezervaciju ({service}: {summary}). Broj rezervacije: {reference}.",
        "ru": "{provider} принял ваше бронирование ({service}: {summary}). Номер брони: {reference}.",
    },
    "txn_in_progress": {
        "en": "Your booking ({service}, {provider}) is under way.",
        "cnr": "Vaša rezervacija ({service}, {provider}) je u toku.",
        "ru": "Ваш заказ ({service}, {provider}) выполняется.",
    },
    "txn_completed": {
        "en": "Your booking ({service}, {provider}) has been completed.",
        "cnr": "Vaša rezervacija ({service}, {provider}) je izvršena.",
        "ru": "Ваш заказ ({service}, {provider}) выполнен.",
    },
    "txn_rejected": {
        "en": "{provider} could not accept your booking ({service}). Nothing has been booked.",
        "cnr": "{provider} nije mogao da prihvati vašu rezervaciju ({service}). Ništa nije rezervisano.",
        "ru": "{provider} не смог принять ваше бронирование ({service}). Ничего не забронировано.",
    },
    "txn_failed": {
        "en": "I'm having trouble reaching {provider}. Nothing has been confirmed yet. I've sent this to the property team.",
        "cnr": "Imam problem da dobijem odgovor od: {provider}. Ništa još nije potvrđeno. Ovo je proslijeđeno osoblju.",
        "ru": "Не удаётся связаться с {provider}. Пока ничего не подтверждено. Это передано сотрудникам.",
    },
    "txn_failed_after_accept": {
        "en": "{provider} reported a problem with your booking ({service}). It is no longer confirmed. "
              "I've informed the property team.",
        "cnr": "{provider} je prijavio problem sa vašom rezervacijom ({service}). Više nije potvrđena. Osoblje je obaviješteno.",
        "ru": "{provider} сообщил о проблеме с вашим бронированием ({service}). Оно больше не подтверждено. "
              "Сотрудники уведомлены.",
    },
    "txn_cancel_requested": {
        "en": "I've asked {provider} to cancel your booking ({service}). It isn't cancelled until they confirm.",
        "cnr": "Od: {provider} je zatraženo otkazivanje vaše rezervacije ({service}). Nije otkazana dok to ne potvrde.",
        "ru": "{provider} получил просьбу отменить ваше бронирование ({service}). Оно не отменено, пока они не подтвердят.",
    },
    "txn_cancelled": {
        "en": "Your booking ({service}) with {provider} has been cancelled.",
        "cnr": "Vaša rezervacija ({service}) kod: {provider} je otkazana.",
        "ru": "Ваше бронирование ({service}) в {provider} отменено.",
    },
    "txn_cancel_refused": {
        "en": "{provider} could not cancel your booking ({service}), so it is still active. I've informed the property team.",
        "cnr": "{provider} nije mogao da otkaže vašu rezervaciju ({service}), pa je i dalje aktivna. Osoblje je obaviješteno.",
        "ru": "{provider} не смог отменить ваше бронирование ({service}), оно остаётся в силе. Сотрудники уведомлены.",
    },
    "txn_change_after_confirm": {
        "en": "Your booking ({service}) has already been sent to {provider} with the earlier details, so I can't change "
              "it here. I've asked the staff to help; nothing has been changed yet.",
        "cnr": "Vaša rezervacija ({service}) je već poslata: {provider}, sa ranijim podacima, pa je ovdje ne mogu "
               "promijeniti. Osoblje je zamoljeno da pomogne; ništa još nije promijenjeno.",
        "ru": "Ваше бронирование ({service}) уже отправлено в {provider} с прежними данными, поэтому изменить его здесь "
              "нельзя. Мы попросили сотрудников помочь; пока ничего не изменено.",
    },
    "quote_pending_status": {
        "en": "You have a price offer ({code}, {price}), not a booking. Nothing is booked until you reply \"yes\".",
        "cnr": "Imate ponudu ({code}, {price}), a ne rezervaciju. Ništa nije rezervisano dok ne odgovorite \"da\".",
        "ru": "У вас есть предложение ({code}, {price}), а не бронирование. Ничего не забронировано, пока вы не ответите «да».",
    },
    "ask_pickup_time": {
        "en": "When should the driver pick you up (date and time)?",
        "cnr": "Kada vozač treba da vas sačeka (datum i vrijeme)?",
        "ru": "Когда вас забрать (дата и время)?",
    },
    "ask_pickup": {
        "en": "Where should the driver pick you up? (For example: at the property, or at the airport.)",
        "cnr": "Gdje vozač treba da vas sačeka? (Na primjer: ispred smještaja ili na aerodromu.)",
        "ru": "Откуда вас забрать? (Например: от места проживания или из аэропорта.)",
    },
    "ask_destination": {
        "en": "Where would you like to go?",
        "cnr": "Gdje želite da idete?",
        "ru": "Куда вы хотите поехать?",
    },
    "ask_time_on": {
        "en": "What time on {day}?", "cnr": "U koliko sati, {day}?", "ru": "Во сколько, {day}?",
    },
    "ask_datetime": {
        "en": "For what date and time?", "cnr": "Za koji datum i vrijeme?", "ru": "На какую дату и время?",
    },
    "ask_count": {
        "en": "For how many people?", "cnr": "Za koliko osoba?", "ru": "На сколько человек?",
    },
    "ask_text": {
        "en": "Please tell me the {field}.", "cnr": "Molim vas, navedite: {field}.", "ru": "Пожалуйста, уточните: {field}.",
    },
    "which_offer": {
        "en": "You have several open offers: {offers}. Which should I book? For example \"book {example}\" or \"book all\". Nothing is booked yet.",
        "cnr": "Imate više otvorenih ponuda: {offers}. Koju da rezervišem? Na primjer \"rezerviši {example}\" ili \"rezerviši sve\". Ništa još nije rezervisano.",
        "ru": "У вас несколько открытых предложений: {offers}. Какое забронировать? Например, «забронируй {example}» или «забронируй все». Пока ничего не забронировано.",
    },
    "which_booking": {
        "en": "You have several bookings: {bookings}. Which one do you mean?",
        "cnr": "Imate više rezervacija: {bookings}. Na koju mislite?",
        "ru": "У вас несколько бронирований: {bookings}. Какое вы имеете в виду?",
    },
    "no_availability": {
        "en": "{service}: nothing is available for that ({reason}). {alternatives}Nothing is booked.",
        "cnr": "{service}: nema slobodnih mjesta za to ({reason}). {alternatives}Ništa nije rezervisano.",
        "ru": "{service}: на это время нет мест ({reason}). {alternatives}Ничего не забронировано.",
    },
    "alternatives": {
        "en": "Nearest available: {list}. ", "cnr": "Najbliži slobodni termini: {list}. ",
        "ru": "Ближайшие свободные варианты: {list}. ",
    },
    "reason_no_capacity": {"en": "fully booked", "cnr": "popunjeno", "ru": "всё занято"},
    "reason_not_offered_at_that_time": {"en": "not offered at that time", "cnr": "nije u ponudi u to vrijeme",
                                        "ru": "в это время не предлагается"},
    "reason_language_unavailable": {"en": "nobody available in that language", "cnr": "nema nikoga na tom jeziku",
                                    "ru": "нет никого с этим языком"},
    "reason_party_too_large": {"en": "the group is too large for what is free then",
                               "cnr": "grupa je prevelika za ono što je tada slobodno",
                               "ru": "группа слишком большая для свободных вариантов"},
    "reason_slot_gone": {"en": "just taken", "cnr": "upravo zauzeto", "ru": "только что заняли"},
    "saved": {
        "en": "Saved to your plan: {title}. Nothing is reserved.",
        "cnr": "Sačuvano u vašem planu: {title}. Ništa nije rezervisano.",
        "ru": "Сохранено в ваш план: {title}. Ничего не забронировано.",
    },
    "not_bookable": {
        "en": "{title} can't be reserved through me{walkin}. It stays in the list; nothing is reserved.",
        "cnr": "{title} ne mogu rezervisati{walkin}. Ništa nije rezervisano.",
        "ru": "{title} забронировать через меня нельзя{walkin}. Ничего не забронировано.",
    },
    "walk_in_ok": {"en": " (walk-ins are welcome)", "cnr": " (može i bez rezervacije)", "ru": " (можно прийти без брони)"},
    "plan_header": {"en": "Your plan{ctx}:", "cnr": "Vaš plan{ctx}:", "ru": "Ваш план{ctx}:"},
    "plan_empty": {
        "en": "There is nothing in your plan{ctx} yet.", "cnr": "U vašem planu{ctx} još nema ničega.",
        "ru": "В вашем плане{ctx} пока ничего нет.",
    },
    "trip_header": {
        "en": "Here is your plan. Each item has its own status - nothing is booked until you confirm it:",
        "cnr": "Evo vašeg plana. Svaka stavka ima svoj status - ništa nije rezervisano dok ne potvrdite:",
        "ru": "Вот ваш план. У каждого пункта свой статус — ничего не забронировано, пока вы не подтвердите:",
    },
    "trip_footer": {
        "en": "To book, reply for example \"book {example}\" (or name the item, e.g. \"book the transfer\"), or \"book all\".",
        "cnr": "Za rezervaciju odgovorite npr. \"rezerviši {example}\" (ili navedite stavku), ili \"rezerviši sve\".",
        "ru": "Чтобы забронировать, ответьте, например, «забронируй {example}» (или назовите пункт), или «забронируй все».",
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


# Plan item status labels (effective status read from stored state).
STATUS_LABELS: dict[str, dict[str, str]] = {
    "saved": {"en": "saved", "cnr": "sačuvano", "ru": "сохранено"},
    "shortlisted": {"en": "shortlisted", "cnr": "u užem izboru", "ru": "в списке вариантов"},
    "proposed": {"en": "proposed", "cnr": "predloženo", "ru": "предложено"},
    "offered": {"en": "price offered - not booked", "cnr": "ponuda - nije rezervisano", "ru": "есть цена - не забронировано"},
    "accepted_by_guest": {"en": "sending", "cnr": "šalje se", "ru": "отправляется"},
    "expired": {"en": "offer expired", "cnr": "ponuda istekla", "ru": "предложение истекло"},
    "unavailable": {"en": "not available", "cnr": "nije dostupno", "ru": "недоступно"},
    "proposed_action": {"en": "sending", "cnr": "šalje se", "ru": "отправляется"},
    "submitted": {"en": "requested - not confirmed", "cnr": "zatraženo - nije potvrđeno", "ru": "запрошено - не подтверждено"},
    "accepted": {"en": "CONFIRMED by provider", "cnr": "POTVRĐENO", "ru": "ПОДТВЕРЖДЕНО"},
    "in_progress": {"en": "in progress", "cnr": "u toku", "ru": "выполняется"},
    "completed": {"en": "completed", "cnr": "izvršeno", "ru": "выполнено"},
    "rejected": {"en": "declined by provider", "cnr": "odbijeno", "ru": "отклонено"},
    "failed": {"en": "failed - staff informed", "cnr": "neuspješno - osoblje obaviješteno", "ru": "ошибка - сотрудники уведомлены"},
    "cancelled": {"en": "cancelled", "cnr": "otkazano", "ru": "отменено"},
}


def status_label(status: str, locale: str) -> str:
    entry = STATUS_LABELS.get(status, {"en": status})
    if locale == "cnr-Cyrl":
        return latin_to_cyrillic(entry.get("cnr", entry["en"]))
    return entry.get(locale.split("-")[0]) or entry["en"]
