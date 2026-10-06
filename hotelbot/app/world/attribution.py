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
    """`source_ids` come from the projection, which already decided that
    attribution is owed (current or historical terms); the text is the
    current one, else the last text the source required."""
    label = _LABEL.get(locale.split("-")[0], _LABEL["en"])
    out = []
    for row in session.scalars(select(WorldSourceRow).where(WorldSourceRow.id.in_(source_ids or {""}))
                               .order_by(WorldSourceRow.id)):
        text = row.attribution_text or next((h.get("attribution_text") for h in reversed(
            (row.config or {}).get("terms_history", [])) if h.get("attribution_text")), None)
        if text:
            out.append(label.format(t=text))
    return out
