"""Rendering discovery results for the guest - from structured fields only."""

from __future__ import annotations

from datetime import datetime

from app.discovery.engine import Candidate, EventCandidate, walking
from app.places.hours import OpenState
from app.places.taxonomy import label
from app.agent.messages import to_cyrillic_template

_L = {
    "open_until": {"en": "open until {t}", "cnr": "otvoreno do {t}", "ru": "открыто до {t}"},
    "open_24_7": {"en": "open 24/7", "cnr": "otvoreno 24/7", "ru": "открыто круглосуточно"},
    "opens_at": {"en": "opens at {t}", "cnr": "otvara se u {t}", "ru": "откроется в {t}"},
    "closed": {"en": "closed at that time", "cnr": "zatvoreno u to vrijeme", "ru": "закрыто в это время"},
    "closed_now": {"en": "closed now", "cnr": "sada zatvoreno", "ru": "сейчас закрыто"},
    "closed_reason": {"en": "temporarily closed ({r})", "cnr": "privremeno zatvoreno ({r})", "ru": "временно закрыто ({r})"},
    "hours_disputed": {"en": "opening hours disputed", "cnr": "radno vrijeme sporno", "ru": "часы работы спорные"},
    "hours_unknown": {"en": "no opening-hours data", "cnr": "nema podataka o radnom vremenu",
                      "ru": "нет данных о часах работы"},
    "kitchen_until": {"en": "kitchen until {t}", "cnr": "kuhinja do {t}", "ru": "кухня до {t}"},
    "walk": {"en": "{km} km from {anchor} (straight line, ~{m} min walk)",
             "cnr": "{km} km od: {anchor} (vazdušnom linijom, ~{m} min pješke)",
             "ru": "{km} км от: {anchor} (по прямой, ~{m} мин пешком)"},
    "kitchen_closed_now": {"en": "kitchen closed", "cnr": "kuhinja ne radi", "ru": "кухня закрыта"},
    "kitchen_opens": {"en": "kitchen opens {t}", "cnr": "kuhinja od {t}", "ru": "кухня с {t}"},
    "kitchen_unknown": {"en": "kitchen hours not known - I can't say if food is served",
                        "cnr": "radno vrijeme kuhinje nepoznato", "ru": "часы кухни неизвестны"},
    "fresh_stale": {"en": "{fact} last verified {d} days ago - may have changed, please check",
                    "cnr": "{fact} provjereno prije {d} dana - moguće izmjene",
                    "ru": "{fact}: проверено {d} дн. назад - могли измениться"},
    "fresh_unknown": {"en": "{fact} never verified - can't confirm", "cnr": "{fact} nije provjereno",
                      "ru": "{fact}: не проверено - подтвердить не могу"},
    "fact_hours": {"en": "opening hours", "cnr": "radno vrijeme", "ru": "часы работы"},
    "fact_kitchen": {"en": "kitchen hours", "cnr": "radno vrijeme kuhinje", "ru": "часы кухни"},
    "fact_closure": {"en": "closure information", "cnr": "informacija o zatvaranju", "ru": "данные о закрытии"},
    "fact_event": {"en": "event details", "cnr": "detalji događaja", "ru": "данные о событии"},
    "dress": {"en": "dress code: {v}", "cnr": "pravila oblačenja: {v}", "ru": "дресс-код: {v}"},
    "sold_out": {"en": "SOLD OUT", "cnr": "RASPRODATO", "ru": "БИЛЕТОВ НЕТ"},
    "conflict": {"en": "sources disagree about the {fact} - please check before going",
                 "cnr": "izvori se ne slažu oko: {fact} - provjerite prije polaska",
                 "ru": "источники расходятся: {fact} — проверьте перед визитом"},
    "existence:unconfirmed": {"en": "no longer listed by its source - may have closed, please check",
                              "cnr": "izvor ga više ne navodi - moguće da je zatvoreno",
                              "ru": "источник его больше не показывает — возможно, закрылось"},
    "fact_start": {"en": "start time", "cnr": "vrijeme početka", "ru": "время начала"},
    "none_because": {"en": "I couldn't find anything that fits{ctx} in my local data: {why}. I won't guess - would you like me to ask the staff?",
                     "cnr": "U mojim lokalnim podacima nema ničega što odgovara{ctx}: {why}. Ne nagađam - da pitam osoblje?",
                     "ru": "В моих местных данных ничего подходящего не нашлось{ctx}: {why}. Гадать не буду — спросить у сотрудников?"},
    "why": {"en": "{n} {what}", "cnr": "{n} {what}", "ru": "{n} {what}"},
    "rej_closed_at_time": {"en": "closed at that time", "cnr": "zatvoreno u to vrijeme", "ru": "закрыто в это время"},
    "rej_temporarily_closed": {"en": "temporarily closed", "cnr": "privremeno zatvoreno", "ru": "временно закрыто"},
    "rej_kitchen_closed": {"en": "kitchen closed by then", "cnr": "kuhinja zatvorena", "ru": "кухня уже закрыта"},
    "rej_closes_too_early": {"en": "close too early", "cnr": "zatvaraju ranije", "ru": "закрываются раньше"},
    "rej_closes_before_window_ends": {"en": "close before your window ends", "cnr": "zatvaraju ranije",
                                      "ru": "закрываются раньше, чем закончится ваше время"},
    "rej_does_not_fit_window": {"en": "need more time than you have", "cnr": "traže više vremena",
                                "ru": "требуют больше времени, чем у вас есть"},
    "rej_outside_radius": {"en": "too far", "cnr": "predaleko", "ru": "слишком далеко"},
    "rej_group_too_large": {"en": "too small for your group", "cnr": "premalo za vašu grupu",
                            "ru": "слишком малы для вашей группы"},
    "rej_age_restricted": {"en": "age-restricted", "cnr": "starosno ograničenje", "ru": "есть возрастное ограничение"},
    "rej_too_expensive": {"en": "above your budget", "cnr": "preko budžeta", "ru": "дороже бюджета"},
    "rej_excluded_kind": {"en": "excluded by you", "cnr": "isključeno", "ru": "исключено вами"},
    "rej_requires": {"en": "lack what you need", "cnr": "nemaju ono što tražite", "ru": "не подходят по требованиям"},
    "rej_excluded": {"en": "excluded by you", "cnr": "isključeno", "ru": "исключено вами"},
    "rej_opposite_of_preference": {"en": "the opposite of what you asked", "cnr": "suprotno od traženog",
                                   "ru": "противоположны запросу"},
    "rej_state_unknown": {"en": "with unknown hours (left out because open ones exist)",
                          "cnr": "bez podataka o radnom vremenu", "ru": "без данных о часах работы"},
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
    "no_ticket": {"en": "no ticket needed", "cnr": "bez karte", "ru": "билет не нужен"},
    "ticket": {"en": "ticket {p} {c}", "cnr": "karta {p} {c}", "ru": "билет {p} {c}"},
}


def L(key: str, locale: str, **kw: object) -> str:
    entry = _L[key]
    if locale == "cnr-Cyrl":
        text = to_cyrillic_template(entry["cnr"])
    else:
        text = entry.get(locale.split("-")[0]) or entry["en"]
    return text.format(**kw) if kw else text


def always_open(hours: dict | None) -> bool:
    """Every day 00:00-00:00, no seasonal / special / closure overrides."""
    h = hours or {}
    weekly = h.get("weekly") or {}
    days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    return all(weekly.get(d) == [["00:00", "00:00"]] for d in days) and not (
        h.get("seasonal") or h.get("special") or h.get("closed"))


def _hhmm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "?"


def status_text(c: Candidate, locale: str, *, now: bool = False) -> str:
    """`now`: the state is the CURRENT one (no time was asked)."""
    st = c.status
    if st.state == OpenState.OPEN and c.kitchen is not None and c.kitchen.state != OpenState.OPEN \
            and c.place.category in ("FOOD", "NIGHTLIFE"):
        out = L("open_until", locale, t=_hhmm(st.closes_at))
        if c.kitchen.state == OpenState.OPEN_LATER:
            return out + ", " + L("kitchen_opens", locale, t=_hhmm(c.kitchen.opens_at))
        if c.kitchen.state == OpenState.CLOSED:
            return out + ", " + L("kitchen_closed_now", locale)
        return out
    if st.state == OpenState.OPEN and always_open(c.place.hours):
        out = L("open_24_7", locale)
    elif st.state == OpenState.OPEN:
        out = L("open_until", locale, t=_hhmm(st.closes_at))
    elif st.state == OpenState.OPEN_LATER:
        out = L("opens_at", locale, t=_hhmm(st.opens_at))
    elif st.state == OpenState.CLOSED:
        out = L("closed_reason", locale, r=st.reason) if st.reason else L("closed_now" if now else "closed", locale)
    elif ((c.place.resolution or {}).get("opening_hours") or {}).get("state") in ("needs_verification",
                                                                                 "conflicted"):
        out = L("hours_disputed", locale)
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
        elif kind == "fresh":
            fact, state, days = v.split(":")
            name = L(f"fact_{fact}", locale) if f"fact_{fact}" in _L else fact
            out.append(L("fresh_stale", locale, fact=name, d=days) if state == "stale"
                       else L("fresh_unknown", locale, fact=name))
        elif kind == "conflict":
            name = L(f"fact_{v}", locale) if f"fact_{v}" in _L else v
            out.append(L("conflict", locale, fact=name))
        elif kind == "dress":
            out.append(L("dress", locale, v=v))
        elif kind == "age":
            out.append(L("age", locale, v=v))
        elif kind == "cover":
            out.append(L("cover", locale, v=v))
        elif kind in _L:
            out.append(L(kind, locale))
    return out


def candidate_line(i: int, c: Candidate, locale: str, anchor: str = "the hotel", *, now: bool = False) -> str:
    parts = [f"{i}. {c.place.name} ({label(c.place.subcategory, locale)})"]
    detail = [status_text(c, locale, now=now)]
    if c.distance_km is not None:
        detail.append(L("walk", locale, km=f"{c.distance_km:.1f}", m=c.walk_minutes or walking(c.distance_km),
                        anchor=anchor))
    detail += _reason_labels(c, locale) + _caveat_labels(c, locale)
    return parts[0] + " - " + "; ".join(detail)


def event_line(i: int, e: EventCandidate, locale: str, tz_fmt) -> str:
    title = e.event.title.get(locale.split("-")[0]) or e.event.title.get("en") or e.event.slug
    where = f" @ {e.place.name}" if e.place else ""
    if e.event.ticket_required and e.event.ticket_price is not None:
        price = L("ticket", locale, p=f"{e.event.ticket_price:.2f}", c=e.event.currency or "")
    elif (e.event.attributes or {}).get("ticketing") == "unknown":
        price = ""                     # the source does not say - neither "free" nor "ticket needed"
    elif not e.event.ticket_required:
        # "free" only when the data says so; otherwise just: no ticket.
        free = e.event.ticket_price == 0 or "free" in set((e.event.tags or {}).get("tags", []))
        price = L("free" if free else "no_ticket", locale)
    else:
        price = ""
    cav = []
    for c in e.caveats:
        if c.startswith("fresh:"):
            _, fact, state, days = c.split(":")
            cav.append(L("fresh_stale", locale, fact=L("fact_event", locale), d=days) if state == "stale"
                       else L("fresh_unknown", locale, fact=L("fact_event", locale)))
        elif c in _L:
            cav.append(L(c, locale))
    if ((e.event.resolution or {}).get("start_at") or {}).get("state") in ("needs_verification", "conflicted"):
        cav.append(L("conflict", locale, fact=L("fact_start", locale)))
    if e.event.age_limit:
        cav.append(L("age", locale, v=e.event.age_limit))
    extra = "; ".join(x for x in [price] + cav if x)
    return f"{i}. {title}{where} - {tz_fmt(e.event.start_at)}" + (f"; {extra}" if extra else "")


def rejection_summary(rejected, locale: str) -> str:  # noqa: ANN001  (collections.Counter)
    """"3 closed at that time, 1 above your budget" - why nothing survived."""
    parts = []
    for reason, n in rejected.most_common():
        key = "rej_" + reason.split(":")[0]
        if key in _L:
            parts.append(L("why", locale, n=n, what=L(key, locale)))
    return ", ".join(dict.fromkeys(parts))
