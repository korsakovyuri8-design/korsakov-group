"""Attribution: what a result must say about where its data came from.

The projection records, per place/event, the sources whose WINNING
assertions require attribution (`resolution._attribution`). Replies that
show such a result add the source's attribution text."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import WorldSourceRow

_LABEL = {"en": "Data: {t}", "cnr": "Podaci: {t}", "ru": "Данные: {t}"}


def required(session: Session, source_ids: set[str]) -> list[WorldSourceRow]:
    if not source_ids:
        return []
    return list(session.scalars(select(WorldSourceRow).where(WorldSourceRow.id.in_(source_ids),
                                                             WorldSourceRow.attribution_required)
                                .order_by(WorldSourceRow.id)))


def lines_for(session: Session, source_ids: set[str], locale: str) -> list[str]:
    label = _LABEL.get(locale.split("-")[0], _LABEL["en"])
    return [label.format(t=row.attribution_text) for row in required(session, source_ids) if row.attribution_text]
