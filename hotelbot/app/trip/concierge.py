"""Local concierge: discovery, plan commands and multi-part trip requests.

Sits next to the transaction dialogue and keeps the states apart:

* DISCOVERY    - "where can I...", "open now", "somewhere lively" ->
                 candidates from the discovery pipeline over structured
                 region data only (never a model's memory); hours, kitchen
                 state, freshness and distance are computed, not guessed.
* SAVE / SHORTLIST / PLAN - "save the second bar", "add the concert to
                 Sunday" -> plan items with a non-transactional status.
                 Nothing is reserved, quoted or confirmed.
* RESERVATION  - "book the first restaurant", "buy tickets for the concert"
                 -> through the marketplace bridge only; the transaction
                 dialogue then quotes and asks for consent. A place or event
                 without an offering is never "booked".
* TRANSACTION / CONFIRMATION - owned by TransactionDialogue and stored state.

A multi-part message ("We arrive Friday around 8pm, four of us. Our transfer
is already booked. Find local food still serving when we arrive. Saturday
we're skiing - somewhere casual for lunch...") is split into independent
items. Statements ("already booked", "we have the guide", "we're skiing")
are trip CONTEXT, never new orders; each request keeps its own status.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.clock import Clock
from app.text import fold
from app.transactions.slots import local_now, parse_count
from app.trip.commands import PLAN_PHRASES, SAVED_PHRASES, PlanCommands  # noqa: F401
from app.trip.compose import T, day_label  # noqa: F401
from app.trip.discovery_dialogue import EXTERNAL, DiscoveryDialogue  # noqa: F401
from app.trip.planner import ARRIVAL, WHEN_WE_ARRIVE, TripPlanner, explicit_day, is_statement, split_requests  # noqa: F401

if TYPE_CHECKING:
    from app.agent.orchestrator import _Turn
    from app.transactions.dialogue import TransactionDialogue


class Concierge(TripPlanner, PlanCommands, DiscoveryDialogue):
    """The concierge = plan commands + discovery dialogue + trip planner,
    sharing the helpers below. Module layout (Iteration 5 split, no
    behaviour change): compose.py (texts, labels, sections), commands.py,
    discovery_dialogue.py, planner.py."""
    def __init__(self, clock: Clock, transactions: TransactionDialogue | None) -> None:
        self.clock = clock
        self.txn = transactions

    # ------------------------------------------------------------ helpers
    def _now_local(self, turn: _Turn) -> datetime:
        return local_now(self.clock.now(), turn.runtime.timezone)

    def _tz(self, turn: _Turn) -> ZoneInfo:
        return ZoneInfo(turn.runtime.timezone)

    def _fmt(self, turn: _Turn, dt: datetime | None, *, with_time: bool = True) -> str:
        if dt is None:
            return ""
        if dt.tzinfo is not None:
            dt = dt.astimezone(self._tz(turn))
        return day_label(dt, turn.language) + (dt.strftime(" %H:%M") if with_time else "")

    def _party(self, turn: _Turn, text: str) -> int | None:
        return parse_count(fold(text)) or turn.stay.party_size or \
            (turn.stay.facts or {}).get("guest_count", {}).get("value")

    @staticmethod
    def _outcome(turn: _Turn, *kinds: str) -> None:
        for k in kinds:
            if k not in turn.outcomes:
                turn.outcomes.append(k)
