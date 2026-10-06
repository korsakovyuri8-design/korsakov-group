"""Plan commands and plan views on shown results: save / shortlist / plan /
book / buy tickets (per clause, by number, ordinal or name), "my plan",
"what did I save?". SAVE and PLAN never transact; booking goes through
the marketplace bridge only."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import TYPE_CHECKING, Any


from app.agent import messages as msg
from app.agent.intents import Intent
from app.clock import as_utc
from app.db.models import Event, ItemStatus, Place
from app.marketplace import bridge
from app.observability import log_event
from app.schemas.messages import ActionTaken
from app.text import contains_phrase, fold
from app.transactions.catalog import VENUE_CATEGORIES
from app.transactions.slots import parse_when
from app.trip import itinerary, schedule, selection

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn
from app.trip.compose import (T, _SAFETY, _hits, day_label)


PLAN_PHRASES = ["my plan", "our plan", "itinerary", "what am i doing", "what are we doing", "what's planned",
                "what is planned", "whats planned", "what do i have", "what do we have", "my schedule",
                "moj plan", "nas plan", "sta imam", "sta imamo", "sta je u planu", "raspored",
                "мой план", "наш план", "что у меня", "что у нас", "что запланировано", "расписание",
                "что мы делаем"]
SAVED_PHRASES = ["what did i save", "what did we save", "what have i saved", "what have we saved", "my saved",
                 "our saved", "show saved", "saved places", "show my saved", "my shortlist", "our shortlist",
                 "sta sam sacuvao", "sta smo sacuvali", "sacuvan*", "что я сохранил*", "что мы сохранил*",
                 "сохранённ*", "сохраненн*", "мои сохран*"]


class PlanCommands:
    # ========================================================== commands
    def handle_command(self, turn: _Turn) -> bool:
        """Plan views, and plan commands on the last shown results:
        "save 2", "book the first restaurant, save the second bar and add
        the concert to Sunday", "buy tickets for the concert"."""
        if turn.intent is not None and turn.intent.intent in _SAFETY:
            return False
        if _hits(turn.text, SAVED_PHRASES) and not selection.parse_commands(turn.text, self._now_local(turn).date()):
            return self._show_saved(turn)
        entries = turn.state.get("last_results") or []
        commands = selection.parse_commands(turn.text, self._now_local(turn).date())
        if commands and entries:
            resolved = selection.resolve_all(commands, entries)
            if resolved.any_resolved:
                return self._run_commands(turn, resolved.commands)
        if _hits(turn.text, PLAN_PHRASES) and turn.intent is not None and \
                turn.intent.intent not in (Intent.SERVICE_REQUEST, Intent.BOOKING_REQUEST):
            return self._show_plan(turn)
        return False

    def _run_commands(self, turn: _Turn, commands: list[selection.Command]) -> bool:
        lines: list[str] = []
        for c in commands:
            if c.entry is None:
                key = "ref_ambiguous" if c.problem == "ambiguous" else "ref_not_found"
                lines.append(T(key, turn.language, ref=c.ref.text or c.text, kind=c.ref.kind or "one"))
                continue
            turn.reply = None
            if c.verb == "save":
                self._save(turn, c.entry, ItemStatus.SAVED)
            elif c.verb == "shortlist":
                self._save(turn, c.entry, ItemStatus.SHORTLISTED)
            elif c.verb == "plan":
                self._plan_add(turn, c.entry, c.day)
            elif c.verb == "tickets":
                self._tickets(turn, c.entry, c.party, clause=c.text)
            else:
                self._book(turn, c.entry, clause=c.text)
            if turn.reply:
                lines.append(turn.reply)
        turn.reply, turn.succeeded = "\n".join(lines), True
        log_event("plan_commands", conversation_id=turn.conv.id,
                  commands=[(c.verb, c.entry.get("name") if c.entry else None, c.problem) for c in commands])
        return True

    def _entry_target(self, turn: _Turn, entry: dict[str, Any]) -> tuple[Place | None, Event | None]:
        if entry.get("event_id"):
            event = turn.session.get(Event, entry["event_id"])
            return (turn.session.get(Place, event.place_id) if event and event.place_id else None), event
        return turn.session.get(Place, entry.get("place_id")), None

    def _title(self, turn: _Turn, place: Place | None, event: Event | None) -> str:
        if event is not None:
            return event.title.get(turn.language.split("-")[0]) or event.title.get("en") or event.slug
        return place.name if place else ""

    def _save(self, turn: _Turn, entry: dict[str, Any], status: ItemStatus) -> bool:
        """SAVE / SHORTLIST: a plan item with a non-transactional status. No
        quote, no hold, no provider call."""
        place, event = self._entry_target(turn, entry)
        if place is None and event is None:
            return False
        title = self._title(turn, place, event)
        starts = as_utc(event.start_at) if event else (
            datetime.fromisoformat(entry["at"]).replace(tzinfo=self._tz(turn)) if entry.get("at") else None)
        item = itinerary.add(turn.session, stay_id=turn.stay.id, kind=(event.category if event else place.category),
                             title=title, status=status, starts_at=starts,
                             place_id=place.id if place and not event else None, event_id=event.id if event else None,
                             details={"subcategory": place.subcategory if place and not event else None})
        turn.actions.append(ActionTaken(kind="plan_item", id=item.id, detail={"status": status.value, "title": title}))
        turn.reply = msg.t("saved", turn.language, title=title) if status == ItemStatus.SAVED else \
            T("shortlisted", turn.language, title=title)
        turn.succeeded = True
        self._outcome(turn, "SAVE")
        return True

    def _plan_add(self, turn: _Turn, entry: dict[str, Any], day: date | None) -> bool:
        """"Add the concert to Sunday": a PLANNED item. Never buys tickets."""
        place, event = self._entry_target(turn, entry)
        tz = self._tz(turn)
        if event is not None:
            title = self._title(turn, place, event)
            start = as_utc(event.start_at).astimezone(tz)
            if day is not None and start.date() != day:
                turn.reply = T("wrong_day", turn.language, title=title, when=self._fmt(turn, start),
                               asked=day_label(day, turn.language))
                turn.succeeded = True
                self._outcome(turn, "ANSWER")
                return True
            tickets = ""
            if event.ticket_required:
                sold = bridge.options_for(turn.session, event=event, capabilities=turn.runtime.capabilities)
                tickets = T("tickets_hint", turn.language, what=event.category if event.category != "other" else
                            "event") if sold else T("tickets_elsewhere", turn.language)
            item = itinerary.add(turn.session, stay_id=turn.stay.id, kind=event.category, title=title,
                                 status=ItemStatus.PLANNED, starts_at=start, event_id=event.id)
            turn.reply = T("planned", turn.language, when=self._fmt(turn, start), title=title, tickets=tickets)
        else:
            assert place is not None
            when = datetime.fromisoformat(entry["at"]).replace(tzinfo=tz) if entry.get("at") else None
            if day is not None and (when is None or when.date() != day):
                when = None
            item = itinerary.add(turn.session, stay_id=turn.stay.id, kind=place.category, title=place.name,
                                 status=ItemStatus.PLANNED, starts_at=when, place_id=place.id,
                                 details={"day": day.isoformat()} if day and when is None else {})
            turn.reply = T("planned", turn.language, when=self._fmt(turn, when), title=place.name, tickets="") \
                if when else T("planned_no_time", turn.language, title=place.name)
        turn.actions.append(ActionTaken(kind="plan_item", id=item.id, detail={"status": "planned", "title": item.title}))
        turn.succeeded = True
        self._outcome(turn, "SAVE")
        return True

    def _book(self, turn: _Turn, entry: dict[str, Any], *, clause: str | None = None) -> bool:
        """DISCOVERY -> TRANSACTION, through the bridge only."""
        place, event = self._entry_target(turn, entry)
        if event is not None:
            return self._tickets(turn, entry, None, clause=clause)
        if place is None:
            return False
        options = [o for o in bridge.options_for(turn.session, place=place, capabilities=turn.runtime.capabilities)
                   if o.service_type in VENUE_CATEGORIES]
        offering = options[0].offering if options else None
        if offering is not None and self.txn is not None:
            preset: dict[str, Any] = {}
            if entry.get("at"):
                preset["reservation_time"] = datetime.fromisoformat(entry["at"]).replace(
                    tzinfo=self._tz(turn)).isoformat()
            if entry.get("party"):
                preset["party_size"] = entry["party"]
            started = self.txn.try_start_service(turn, offering.service_type, preset=preset, venue=place,
                                                 text=clause)
            if started:
                self._outcome(turn, "TRANSACTION")
            return started
        walkin = msg.t("walk_in_ok", turn.language) if (place.attributes or {}).get("walk_in_supported") else ""
        turn.reply = msg.t("not_bookable", turn.language, title=place.name, walkin=walkin)
        turn.succeeded = True
        self._outcome(turn, "ANSWER")
        return True

    def _tickets(self, turn: _Turn, entry: dict[str, Any], party: int | None, *, clause: str | None = None) -> bool:
        """Event -> ticket Offering, through the bridge. An event without a
        ticket offering is never "booked"."""
        place, event = self._entry_target(turn, entry)
        if event is None:
            turn.reply = T("not_an_event", turn.language, title=place.name if place else "")
            self._outcome(turn, "ANSWER")
            return True
        title = self._title(turn, place, event)
        options = [o for o in bridge.options_for(turn.session, event=event, capabilities=turn.runtime.capabilities)
                   if o.service_type == "event_tickets"]
        if not options or self.txn is None:
            unknown = (event.attributes or {}).get("ticketing") == "unknown"
            turn.reply = T("tickets_not_sold" if event.ticket_required or unknown else "no_ticket_needed",
                           turn.language, title=title)
            self._outcome(turn, "ANSWER")
            return True
        start = as_utc(event.start_at).astimezone(self._tz(turn))
        preset: dict[str, Any] = {"event": title, "_event_id": event.id, "_event_day": start.date().isoformat()}
        if party or entry.get("party"):
            preset["party_size"] = party or entry.get("party")
        started = self.txn.try_start_service(turn, "event_tickets", preset=preset, text=clause)
        if started:
            self._outcome(turn, "TRANSACTION")
        return started

    # ------------------------------------------------------------- views
    def _show_plan(self, turn: _Turn) -> bool:
        now_local = self._now_local(turn)
        folded = fold(turn.text)
        parts = parse_when(folded, now_local.date())
        day = parts.day
        evening = any(contains_phrase(folded, w) for w in ("tonight", "this evening", "veceras", "večeras",
                                                           "вечером", "сегодня вечером"))
        if evening and day is None:
            day = now_local.date()
        entries = itinerary.plan(turn.session, turn.stay.id)
        if day is not None:
            entries = schedule.on_day(entries, day, turn.runtime.timezone, evening=evening)
        ctx = ""
        if day:
            ctx = f" ({day_label(day, turn.language)}{T('evening', turn.language) if evening else ''})"
        self._outcome(turn, "ANSWER")
        if not entries:
            turn.reply, turn.succeeded = msg.t("plan_empty", turn.language, ctx=ctx), True
            return True
        lines = [msg.t("plan_header", turn.language, ctx=ctx)]
        for e in entries:
            when = self._fmt(turn, e.starts_at)
            lines.append(f"- {when + ' · ' if when else ''}{e.item.title} - {msg.status_label(e.status, turn.language)}")
        turn.reply, turn.succeeded = "\n".join(lines), True
        return True

    def _show_saved(self, turn: _Turn) -> bool:
        """"What did I save?", "Show my saved bars" - SAVED and SHORTLISTED
        items, optionally of one kind."""
        rest = re.sub(r"\b(show|list|what|did|have|has|i|we|me|my|our|saved|save|shortlist(ed)?|places?|options?|"
                      r"all|the|pokazi|sta|sam|smo|sacuva\w*|покажи|что|я|мы|мои|наши|сохран\w*)\b", " ",
                      fold(turn.text))
        ref = selection.parse_ref(rest)
        kind = ref.kind if ref.kind not in (None, "any") else None
        out = []
        for e in itinerary.plan(turn.session, turn.stay.id):
            if e.status not in ("saved", "shortlisted"):
                continue
            entry = {"kind": "event" if e.item.event_id else "place", "category": e.item.kind,
                     "event_category": e.item.kind, "subcategory": (e.item.details or {}).get("subcategory")}
            if kind and not selection._matches(entry, kind):
                continue
            out.append(e)
        what = ""
        if kind:
            what = f" ({kind.split(':')[-1].replace('_', ' ')})"
        self._outcome(turn, "ANSWER")
        if not out:
            turn.reply, turn.succeeded = T("saved_empty", turn.language, what=what), True
            return True
        lines = [T("saved_header", turn.language, what=what)]
        for e in out:
            when = self._fmt(turn, e.starts_at)
            lines.append(f"- {e.item.title}{' · ' + when if when else ''} - {msg.status_label(e.status, turn.language)}")
        turn.reply, turn.succeeded = "\n".join(lines), True
        return True
