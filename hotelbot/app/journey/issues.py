"""TripIssue: a surfaced problem - recorded once per (item, kind, detail)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TripIssue


def raise_issue(session: Session, trip_id: str, item_id: str | None, kind: str, detail: dict[str, Any]) -> bool:
    for existing in session.scalars(select(TripIssue).where(TripIssue.trip_id == trip_id, TripIssue.kind == kind,
                                                            TripIssue.item_id == item_id)):
        if existing.detail == detail:
            return False
    session.add(TripIssue(trip_id=trip_id, item_id=item_id, kind=kind, detail=detail))
    return True
