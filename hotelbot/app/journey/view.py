"""A trip as a person reads it: one current picture (each item's current
revision and status), what needs attention (issues), and the evidence
behind every status - the provider's own sentences in issue order.
Deterministic: the same trip state always reads the same."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Trip, TripIssue, TripItem, TripObservation, TripSourceRecord
from app.journey.parsers.common import flat


def _day(local: str) -> str:
    d = datetime.fromisoformat(local[:10])
    return f"{d.day} {d:%b}"


def _when(rec: dict) -> str:
    if rec.get("date_only"):
        return _day(rec["local"])
    text = f"{_day(rec['local'])} {rec['local'][11:16]}"
    return text if rec.get("approximate") else f"{text} ({rec['tz'] or 'zone unknown'})"


def _sort_key(item: TripItem) -> str:
    t = item.times
    rec = t.get("departs") or t.get("check_in") or t.get("starts") or t.get("at") or {}
    local = rec.get("local", "9999-12-31")
    if len(local) == 10:                                 # a date: place it at the stated hour if any
        local += "T" + (item.details.get("check_in_from") or "23:59")
    return f"{local}|{item.kind}|{item.identity}"


def _provider_name(session: Session, item: TripItem) -> str | None:
    o = session.scalar(select(TripObservation).where(TripObservation.item_id == item.id,
                                                     TripObservation.field == "provider").limit(1))
    return flat(o.evidence).split(" - ")[0] if o else None


def _line(session: Session, item: TripItem) -> str:
    st, d, t = item.status.upper(), item.details, item.times
    if item.kind == "flight":
        return (f"Flight {d['flight_number']} {d['origin']} -> {d['destination']}: departs {_when(t['departs'])}, "
                f"arrives {_when(t['arrives'])} - {st}")
    if item.kind == "accommodation":
        extra = ", ".join(x for x in (f"check-in from {d['check_in_from']}" if d.get("check_in_from") else "",
                                      f"check-out until {d['check_out_until']}" if d.get("check_out_until") else "")
                          if x)
        return (f"{_provider_name(session, item)}: {_when(t['check_in'])} -> {_when(t['check_out'])}"
                f"{f' ({extra})' if extra else ''} - {st}")
    if item.kind == "restaurant":
        return f"{d['venue']}: {_when(t['starts'])}, party of {d['party']} - {st}"
    if item.kind == "statement":
        return f"Guest said: {d['subject']} in {d['city']} {_day(t['at']['local'])} around {t['at']['local'][11:16]} " \
               f"- STATED (not a booking)"
    return f"{item.kind}: {st}"


def story(session: Session, trip_id: str) -> str:
    trip = session.get(Trip, trip_id)
    items = sorted(session.scalars(select(TripItem).where(TripItem.trip_id == trip_id)), key=_sort_key)
    by_identity = {i.identity: i for i in items}
    out = [f"Trip: {trip.title or trip.id}", "", "Current picture"]
    out += [f"  {_line(session, i)}" for i in items]
    attention = []
    for issue in sorted(session.scalars(select(TripIssue).where(TripIssue.trip_id == trip_id)),
                        key=lambda x: (x.kind, sorted(x.detail.items()).__repr__())):
        d = issue.detail
        if issue.kind == "guest_provider_disagreement":
            f = by_identity.get(d["provider_item"])
            who = f"{_provider_name(session, f)} {f.details['flight_number']}" if f else "the provider"
            attention.append(f"the guest said around {d['guest_local'][11:16]}, {who} arrives "
                             f"{d['provider_local'][11:16]} - {d['difference_minutes']} min apart (tolerance "
                             f"{d['tolerance_minutes']} min, policy {d['policy_version']}); the booking stands")
        else:
            attention.append(f"{issue.kind.replace('_', ' ')}: {d}")
    out += ["", "Needs attention"] + ([f"  - {a}" for a in attention] or ["  (nothing)"])
    out += ["", "Evidence"]
    for i in items:
        rows = session.execute(select(TripObservation, TripSourceRecord).join(
            TripSourceRecord, TripSourceRecord.id == TripObservation.record_id).where(
            TripObservation.item_id == i.id, TripObservation.field == "status_event")).all()
        rows.sort(key=lambda r: ((r[1].issued_at or r[1].received_at).isoformat()[:16], r[0].value))
        out.append(f"  {_line(session, i).split(':')[0]}")
        for obs, rec in rows:
            when = (rec.issued_at or rec.received_at).strftime("%Y-%m-%d %H:%M UTC")   # stored in UTC
            out.append(f"    {when} {obs.value}: \"{flat(obs.evidence)}\" ({rec.kind}, {rec.source_id})")
        if i.current_revision > 1:
            out.append(f"    revision {i.current_revision} of {len(i.revisions)}; changed: "
                       f"{', '.join(i.revisions[-1]['changed_fields']) or '-'}")
    return "\n".join(out) + "\n"
