"""Guest statements vs provider facts (D-082). The provider value stands; the
statement is kept as its own item. The comparison is recorded on the
statement (`related`, derived) and a difference beyond the policy tolerance
is a TripIssue. No fuzzy merge, and nothing on the provider item changes.
Recomputed from all items after every import, so the import order does
not matter."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TripItem
from app.journey import policies, timeinfo
from app.journey.issues import raise_issue


def _minutes(a: dict, b: dict) -> int:
    if a.get("utc") and b.get("utc"):
        delta = datetime.fromisoformat(a["utc"]) - datetime.fromisoformat(b["utc"])
    else:                                               # same city: compare wall times
        delta = datetime.fromisoformat(a["local"]) - datetime.fromisoformat(b["local"])
    return abs(int(delta.total_seconds() // 60))


def reconcile(session: Session, trip_id: str) -> None:
    pol = policies.load()
    tolerance = pol["tolerances"]["arrival_time_minutes"]
    items = list(session.scalars(select(TripItem).where(TripItem.trip_id == trip_id)))
    flights = [i for i in items if i.kind == "flight" and i.status != "cancelled"]
    for stmt in (i for i in items if i.kind == "statement" and i.details.get("subject") == "arrival"):
        at = stmt.times.get("at") or {}
        airports = set(timeinfo.city_airports(stmt.details.get("city") or ""))
        related = []
        for f in sorted(flights, key=lambda x: x.identity):
            arr = f.times.get("arrives") or {}
            if f.details.get("destination") not in airports or (arr.get("local") or "")[:10] != (at.get("local") or "")[:10]:
                continue
            diff = _minutes(at, arr)
            within = diff <= tolerance
            related.append({"item": f.identity, "field": "arrives", "difference_minutes": diff,
                            "within_tolerance": within, "tolerance_minutes": tolerance,
                            "policy_version": pol["version"]})
            if not within:
                raise_issue(session, trip_id, stmt.id, "guest_provider_disagreement",
                            {"provider_item": f.identity, "field": "arrives", "guest_local": at.get("local"),
                             "provider_local": arr.get("local"), "difference_minutes": diff,
                             "tolerance_minutes": tolerance, "policy_version": pol["version"],
                             "statement_revision": stmt.current_revision})
        stmt.details = {**stmt.details, "related": related}
    session.flush()
