"""Rendering discovery results for the guest - from structured fields only."""

from __future__ import annotations

from datetime import datetime

from app.discovery.engine import Candidate, EventCandidate, walking
from app.places.hours import OpenState
from app.places.taxonomy import label
from app.agent.messages import to_cyrillic_template

_L = {
    "open_until": {"en": "open until {t}", "cnr": "otvoreno do {t}", "ru": "открыто до {t}"},
    "opens_at": {"en": "opens at {t}", "cnr": "otvara se u {t}", "ru": "откроется в {t}"},
    "closed": {"en": "closed at that time", "cnr": "zatvoreno u to vrijeme", "ru": "закрыто в это время"},
    "closed_reason": {"en": "temporarily closed ({r})", "cnr": "privremeno zatvoreno ({r})", "ru": "временно закрыто ({r})"},
    "hours_unknown": {"en": "no opening-hours data", "cnr": "nema podataka o radnom vremenu",
                      "ru": "нет данных о часах работы"},
    "kitchen_until": {"en": "kitchen until {t}", "cnr": "kuhinja do {t}", "ru": "кухня до {t}"},
    "walk": {"en": "{km} km, ~{m} min walk", "cnr": "{km} km, ~{m} min pješke", "ru": "{km} км, ~{m} мин пешком"},
    "reservation_required": {"en": "reservation required", "cnr": "obavezna rezervacija", "ru": "нужна бронь"},
    "age": {"en": "{v}+ only", "cnr": "samo {v}+", "ru": "только {v}+"},
    "cover": {"en": "entry {v} EUR", "cnr": "ulaz {v} EUR", "ru": "вход {v} EUR"},
    "data:stale": {"en": "details not recently verified", "cnr": "podaci nijesu skoro provjereni",
                   "ru": "данные давно не проверялись"},
    "data:unverified": {"en": "details unverified", "cnr": "podaci nijesu provjereni", "ru": "данные не проверены"},
    "vegetarian_options": {"en": "vegetarian options", "cnr": "vegetarijanska jela", "ru": "вегетарианские блюда"},
    "vegan_options": {"en": "vegan options", "cnr": "veganska jela", "ru": "веганские блюда"},
    "gluten_free_options": {"en": "gluten-free options", "cnr": "jela bez glutena", "ru": "без глютена"},
    "wheelchair_access": {"en": "wheelchair accessible", "cnr": "pristupačno za kolica", "ru": "доступно для колясок"},
    "child_friendly": {"en": "child friendly", "cnr": "pogodno za djecu", "ru": "подходит для детей"},
    "pets_allowed": {"en": "pets allowed", "cnr": "ljubimci dozvoljeni", "ru": "можно с животными"},
    "live_music": {"en": "live music", "cnr": "svirka uživo", "ru": "живая музыка"},
    "outdoor_seating": {"en": "outdoor seating", "cnr": "bašta", "ru": "столики на улице"},
    "wifi": {"en": "Wi-Fi", "cnr": "Wi-Fi", "ru": "Wi-Fi"},
    "cuisine:montenegrin": {"en": "Montenegrin cuisine", "cnr": "crnogorska kuhinja", "ru": "черногорская кухня"},
    "tag:lively": {"en": "lively", "cnr": "živahno", "ru": "оживлённо"},
    "tag:quiet": {"en": "quiet", "cnr": "mirno", "ru": "тихо"},
    "tag:jazz": {"en": "jazz", "cnr": "džez", "ru": "джаз"},
    "tag:local": {"en": "local favourite", "cnr": "domaće", "ru": "местное"},
    "header": {"en": "Here is what I found{ctx}:", "cnr": "Evo rezultata{ctx}:", "ru": "Вот что нашлось{ctx}:"},
    "header_essential": {"en": "Nearest {what}{ctx}:", "cnr": "Najbliže ({what}){ctx}:", "ru": "Ближайшие ({what}){ctx}:"},
    "ctx_at": {"en": " for {t}", "cnr": " za {t}", "ru": " на {t}"},
    "none": {"en": "I couldn't find anything matching that in my local data{ctx}. I won't guess - would you like me to ask the staff?",
             "cnr": "U mojim lokalnim podacima nema ničega što odgovara{ctx}. Ne bih da nagađam - želite li da pitam osoblje?",
             "ru": "В моих местных данных ничего подходящего не нашлось{ctx}. Гадать не буду — спросить у сотрудников?"},
    "footer": {"en": "Reply \"save 1\" to keep an option for later{book}. Nothing is reserved yet.",
               "cnr": "Odgovorite \"sačuvaj 1\" da zapamtim opciju{book}. Ništa još nije rezervisano.",
               "ru": "Ответьте «сохрани 1», чтобы запомнить вариант{book}. Пока ничего не забронировано."},
    "footer_book": {"en": ", or \"book 1\" to reserve where possible", "cnr": " ili \"rezerviši 1\" za rezervaciju gdje je moguće",
                    "ru": " или «забронируй 1», где это возможно"},
    "events_header": {"en": "Events{ctx}:", "cnr": "Događaji{ctx}:", "ru": "События{ctx}:"},
    "free": {"en": "free entry", "cnr": "ulaz slobodan", "ru": "вход свободный"},
    "ticket": {"en": "ticket {p} {c}", "cnr": "karta {p} {c}", "ru": "билет {p} {c}"},
}


def L(key: str, locale: str, **kw: object) -> str:
    entry = _L[key]
    if locale == "cnr-Cyrl":
        text = to_cyrillic_template(entry["cnr"])
    else:
        text = entry.get(locale.split("-")[0]) or entry["en"]
    return text.format(**kw) if kw else text


def _hhmm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "?"


def status_text(c: Candidate, locale: str) -> str:
    st = c.status
    if st.state == OpenState.OPEN:
        out = L("open_until", locale, t=_hhmm(st.closes_at))
    elif st.state == OpenState.OPEN_LATER:
        out = L("opens_at", locale, t=_hhmm(st.opens_at))
    elif st.state == OpenState.CLOSED:
        out = L("closed_reason", locale, r=st.reason) if st.reason else L("closed", locale)
    else:
        out = L("hours_unknown", locale)
    if c.kitchen is not None and c.kitchen.state == OpenState.OPEN:
        out += ", " + L("kitchen_until", locale, t=_hhmm(c.kitchen.closes_at))
    return out


def _reason_labels(c: Candidate, locale: str) -> list[str]:
    out = []
    for r in c.reasons:
        kind, _, rest = r.partition(":")
        if kind in ("attr", "pref") and rest in _L:
            out.append(L(rest, locale))
        elif kind == "match":
            key, _, values = rest.partition(":")
            out += [L(f"{key}:{v}", locale) for v in values.split(",") if f"{key}:{v}" in _L]
        elif kind == "tag" and r in _L:
            out.append(L(r, locale))
    return list(dict.fromkeys(out))


def _caveat_labels(c: Candidate, locale: str) -> list[str]:
    out = []
    for cv in c.caveats:
        kind, _, v = cv.partition(":")
        if cv in _L:
            out.append(L(cv, locale))
        elif kind == "age":
            out.append(L("age", locale, v=v))
        elif kind == "cover":
            out.append(L("cover", locale, v=v))
        elif kind in _L:
            out.append(L(kind, locale))
    return out


def candidate_line(i: int, c: Candidate, locale: str) -> str:
    parts = [f"{i}. {c.place.name} ({label(c.place.subcategory, locale)})"]
    detail = [status_text(c, locale)]
    if c.distance_km is not None:
        detail.append(L("walk", locale, km=f"{c.distance_km:.1f}", m=walking(c.distance_km)))
    detail += _reason_labels(c, locale) + _caveat_labels(c, locale)
    return parts[0] + " - " + "; ".join(detail)


def event_line(i: int, e: EventCandidate, locale: str, tz_fmt) -> str:
    title = e.event.title.get(locale.split("-")[0]) or e.event.title.get("en") or e.event.slug
    where = f" @ {e.place.name}" if e.place else ""
    if e.event.ticket_required and e.event.ticket_price is not None:
        price = L("ticket", locale, p=f"{e.event.ticket_price:.2f}", c=e.event.currency or "")
    elif not e.event.ticket_required:
        price = L("free", locale)
    else:
        price = ""
    extra = "; ".join(x for x in [price] + [L(c, locale) for c in e.caveats if c in _L] if x)
    return f"{i}. {title}{where} - {tz_fmt(e.event.start_at)}" + (f"; {extra}" if extra else "")
