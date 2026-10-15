"""Import trip documents: record -> observations -> item, with revisions.

Two-level idempotency (D-077..D-083):
* the same bytes again are the same TripSourceRecord (raw SHA256) - nothing
  new is stored;
* a forwarded / reformatted copy is a NEW record, but its observations go to
  the SAME TripItem, found by semantic identity (provider + booking reference
  + segment), and to the revision whose values it repeats.

A provider document with different values is a new REVISION of the item; the
current revision is the one the provider issued last (its own issue time,
not the order documents arrive in). Status is never a revision: it comes only
from explicit status sentences, and CANCELLED dominates (D-081)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Trip, TripItem, TripObservation, TripSourceRecord
from app.journey import timeinfo
from app.journey.issues import raise_issue
from app.journey.kinds import KINDS
from app.journey.parsers import airline, guest, hotel, restaurant
from app.journey.reconcile import reconcile

PARSERS = {"airline_email": ("parser:airline", airline.parse), "hotel_email": ("parser:hotel", hotel.parse),
           "restaurant_email": ("parser:restaurant", restaurant.parse),
           "guest_statement": ("parser:guest", guest.parse)}


@dataclass
class ImportReport:
    record: TripSourceRecord
    record_new: bool
    item: TripItem | None
    item_new: bool = False
    new_revision: int | None = None
    issues: list[str] = field(default_factory=list)


@dataclass
class ProposalResult:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[dict[str, Any], str]] = field(default_factory=list)


def create_trip(session: Session, title: str | None = None) -> Trip:
    trip = Trip(title=title)
    session.add(trip)
    session.flush()
    return trip


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized_text(raw: str) -> str:
    """The document's content without transport: outer headers, forwarding
    banners, quoting and spacing removed (used to recognise copies)."""
    lines, body = [], False
    for line in raw.splitlines():
        if not body:
            body = line.strip() == ""
            continue
        text = re.sub(r"^(>\s?)+", "", line)
        flat = re.sub(r"\s+", " ", text).strip()
        if not flat or flat.startswith("-----") or re.match(r"^(From|To|Date|Subject|Sent):", flat):
            continue
        lines.append(flat)
    return "\n".join(lines)


def validate_details(kind: str, details: dict[str, Any]) -> None:
    spec = KINDS[kind].details
    unknown = set(details) - set(spec)
    if unknown:
        raise ValueError(f"{kind}: unknown details {sorted(unknown)}")
    for key, typ in spec.items():
        if key in details and not isinstance(details[key], typ):
            raise ValueError(f"{kind}.{key}: wrong type {type(details[key]).__name__}")


def _issue(session: Session, trip_id: str, item_id: str | None, kind: str, detail: dict[str, Any]) -> bool:
    return raise_issue(session, trip_id, item_id, kind, detail)


def _comparable(times: dict[str, Any]) -> dict[str, Any]:
    return {k: {f: v.get(f) for f in ("local", "tz", "place")} for k, v in times.items()}


def _resolve_status(session: Session, item: TripItem) -> str:
    """Status from status EVENTS in the provider's issue order (D-081):
    confirm -> confirmed; cancel -> cancelled; reinstate -> confirmed;
    state (a guest's own statement) -> stated, never above provider evidence.
    CANCELLED dominates: a confirmation issued after it (or an old one
    forwarded again) does not revive the booking - it is surfaced; only an
    explicit reinstatement does. Events without an issue time order first."""
    rows = session.execute(select(TripObservation, TripSourceRecord).join(
        TripSourceRecord, TripSourceRecord.id == TripObservation.record_id).where(
        TripObservation.item_id == item.id, TripObservation.field == "status_event",
        TripObservation.extractor.like("parser:%"))).all()
    rows.sort(key=lambda r: (r[1].issued_at is not None, _aware(r[1].issued_at), _aware(r[1].received_at),
                             _aware(r[0].created_at)))
    state = "unknown"
    for obs, rec in rows:
        if obs.value == "cancel":
            state = "cancelled"
        elif obs.value == "reinstate":
            state = "confirmed"
        elif obs.value == "state" and state == "unknown":
            state = "stated"
        elif obs.value == "confirm":
            if state == "cancelled":
                _issue(session, item.trip_id, item.id, "confirmation_after_cancellation", {"record_id": rec.id})
            else:
                state = "confirmed"
    return state


def _utc(dt: datetime | None) -> datetime | None:
    """An aware instant in UTC; a naive one (as the database returns it) is UTC already."""
    from datetime import timezone

    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware(dt: datetime | None) -> datetime:
    from datetime import timezone

    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


SOURCE_FIELDS = ("provider", "booking_reference")


def provenance_violations(session: Session, item: TripItem) -> list[str]:
    """Source-derived fields of the item that no observation supports
    (gate: every resolved field points to evidence). `kind` is parser
    metadata (the document format), not a claim, and is not checked."""
    obs = list(session.scalars(select(TripObservation).where(TripObservation.item_id == item.id)))
    by_field: dict[str, list[TripObservation]] = {}
    for o in obs:
        by_field.setdefault(o.field, []).append(o)
    bad = []
    for f in SOURCE_FIELDS:
        if getattr(item, f) is not None and not any(o.value == getattr(item, f) for o in by_field.get(f, [])):
            bad.append(f)
    if item.status != "unknown" and not by_field.get("status_event"):
        bad.append("status")
    spec = KINDS[item.kind]
    for k, v in (item.details or {}).items():
        if k in spec.derived:
            continue
        if k in spec.anchor_evidence:
            if not by_field.get(spec.anchor_evidence[k]):
                bad.append(k)
        elif not any(o.value == v for o in by_field.get(k, [])):
            bad.append(k)
    ids = {o.id for f in spec.time_fields for o in by_field.get(f, [])}
    for k, rec in (item.times or {}).items():
        if rec.get("evidence") not in ids:
            bad.append(k)
    return sorted(bad)


def import_document(session: Session, trip_id: str, *, kind: str, raw: str, source_id: str,
                    received_at: datetime) -> ImportReport:
    raw_sha = _sha(raw)
    seen = session.scalar(select(TripSourceRecord).where(TripSourceRecord.trip_id == trip_id,
                                                         TripSourceRecord.raw_sha256 == raw_sha))
    if seen is not None:                                   # the same bytes: a re-delivery, nothing new
        obs = session.scalar(select(TripObservation).where(TripObservation.record_id == seen.id).limit(1))
        return ImportReport(seen, False, session.get(TripItem, obs.item_id) if obs else None)
    extractor, parse = PARSERS[kind]
    parsed = parse(raw)
    # instants are stored in UTC: the database keeps no offset, and issue order must not
    # compare wall clocks of different zones
    issued_utc = _utc(parsed.issued_at) if parsed else None
    record = TripSourceRecord(trip_id=trip_id, kind=kind, source_id=source_id, received_at=_utc(received_at),
                              issued_at=issued_utc, raw=raw, raw_sha256=raw_sha,
                              normalized_sha256=_sha(normalized_text(raw)))
    session.add(record)
    session.flush()
    if parsed is None:
        _issue(session, trip_id, None, "unparsed_document", {"record_id": record.id, "kind": kind})
        session.flush()
        return ImportReport(record, True, None, issues=["unparsed_document"])

    spec = KINDS[parsed.kind]
    values = {f: v for f, v, _ in parsed.observations}
    item, identity, outcome = spec.resolve(session, trip_id, parsed, values)
    if outcome == "ambiguous":                             # never guess which item a document is about
        _issue(session, trip_id, None, "ambiguous_segment_identity",
               {"record_id": record.id, "segment": identity, "doc_type": parsed.doc_type})
        session.flush()
        return ImportReport(record, True, None, issues=["ambiguous_segment_identity"])
    item_new = item is None
    if item_new:
        item = TripItem(trip_id=trip_id, kind=parsed.kind, identity=identity, provider=parsed.provider,
                        booking_reference=parsed.booking_reference, status="unknown", times={},
                        current_revision=0, revisions=[], details=spec.anchors(values))
        session.add(item)
        session.flush()

    # observations first (their ids are the evidence of the time records)
    obs_rows = []
    for fld, value, evidence in parsed.observations:
        o = TripObservation(trip_id=trip_id, record_id=record.id, item_id=item.id, field=fld, value=value,
                            evidence=evidence, revision=0, extractor=extractor)
        session.add(o)
        obs_rows.append(o)
    session.flush()

    details = {f: values.get(f) for f in spec.detail_fields if f in values}
    validate_details(parsed.kind, details)
    times, issues = {}, []
    for o in obs_rows:
        if o.field in spec.time_fields:
            rec, problem = timeinfo.record_from_value(o.value, evidence=o.id)
            times[o.field] = rec
            if problem:
                issues.append(problem)
                _issue(session, trip_id, item.id, problem, {"field": o.field, "raw": o.value["raw"],
                                                            "place": o.value["place"]})

    # which revision does this document state? A field the document does not
    # mention is NOT a change (a schedule-change notice lists no passengers).
    revisions = [dict(r) for r in item.revisions]
    revisions.sort(key=lambda r: (r["issued_at"] or "", r["revision"]))

    def effective(upto: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        d, t = {}, {}
        for r in upto:
            d.update(r["details"])
            t.update(r["times"])
        return d, t

    def states_same(r: dict[str, Any]) -> bool:
        d, t = effective(revisions[: revisions.index(r) + 1])
        return all(d.get(k) == v for k, v in details.items()) and \
            all(_comparable({k: t[k]}) == _comparable({k: v}) if k in t else False for k, v in times.items())

    match = next((r for r in reversed(revisions) if states_same(r)), None)
    new_revision = None
    if match is not None:
        match["record_ids"] = sorted(set(match["record_ids"]) | {record.id})
        seq = match["revision"]
    else:
        seq = max((r["revision"] for r in revisions), default=0) + 1
        new_revision = seq if revisions else None
        revisions.append({"revision": seq, "record_ids": [record.id],
                          "issued_at": issued_utc.isoformat() if issued_utc else None,
                          "details": details, "times": times, "changed_fields": []})
        revisions.sort(key=lambda r: (r["issued_at"] or "", r["revision"]))
    for i, cur in enumerate(revisions):                    # what each revision changed vs. everything before it
        d, t = effective(revisions[:i])
        changed = {k for k, v in cur["details"].items() if i and d.get(k) != v}
        changed |= {k for k, v in cur["times"].items() if i and _comparable({k: t.get(k, {})}) != _comparable({k: v})}
        cur["changed_fields"] = sorted(changed)
    for o in obs_rows:
        o.revision = seq
    current = revisions[-1]                                # the one the provider issued last
    item.revisions = revisions
    item.current_revision = current["revision"]
    anchored = {k: item.details[k] for k in spec.anchors(values) if k in item.details}   # never moves
    kept = {k: item.details[k] for k in spec.derived if k in item.details}                  # recomputed by code
    item.details, item.times = effective(revisions)
    item.details = {**item.details, **anchored, **kept}
    validate_details(parsed.kind, item.details)
    session.flush()
    item.status = _resolve_status(session, item)
    session.flush()
    reconcile(session, trip_id)
    return ImportReport(record, True, item, item_new, new_revision, issues)


def accept_proposals(session: Session, record_id: str, item_id: str,
                     proposals: list[dict[str, Any]]) -> ProposalResult:
    """Observations proposed by a non-deterministic extractor (a model).
    Kept only with evidence that literally occurs in the record; a model
    may never set a status (D-081: status needs a provider sentence read
    by a deterministic parser). Accepted proposals are observations only -
    they do not change the item's resolved values."""
    record = session.get(TripSourceRecord, record_id)
    out = ProposalResult()
    for p in proposals:
        evidence = p.get("evidence")
        if p.get("field") in ("status", "status_event"):
            out.rejected.append((p, "status needs provider evidence read by a deterministic parser"))
        elif not evidence or evidence not in record.raw:
            out.rejected.append((p, "evidence not found in the source record"))
        else:
            session.add(TripObservation(trip_id=record.trip_id, record_id=record.id, item_id=item_id,
                                        field=p["field"], value=p.get("value"), evidence=evidence, revision=0,
                                        extractor=f"model:{p.get('extractor', 'unknown')}"[:32]))
            out.accepted.append(p)
    session.flush()
    return out
