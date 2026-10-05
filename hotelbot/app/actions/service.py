"""ActionService: the only code that changes an action's status.

Every transition is validated against the lifecycle and recorded as an
ActionEvent (who, from, to, why). Guest-facing statements about actions are
derived from this state (app/agent/authority.py), never from model prose.

    PROPOSED ──► SUBMITTED ──► ACCEPTED ──► IN_PROGRESS ──► COMPLETED
       │             │            │              │
       ├─► FAILED    ├─► REJECTED ├─► FAILED     ├─► FAILED
       └─► CANCELLED ├─► FAILED   └─► CANCELLED  └─► CANCELLED
                     ├─► IN_PROGRESS / COMPLETED (staff may skip steps)
                     └─► CANCELLED
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.actions.catalog import default_urgency
from app.actions.executors import ExecutionOutcome, ExecutorRegistry
from app.db.models import (
    TERMINAL_ACTION_STATUSES,
    Action,
    ActionEvent,
    ActionStatus,
    Urgency,
    utcnow,
)
from app.observability import log_event

S = ActionStatus
ALLOWED_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    S.PROPOSED: frozenset({S.SUBMITTED, S.FAILED, S.CANCELLED}),
    S.SUBMITTED: frozenset({S.ACCEPTED, S.PENDING_CONDITION, S.REJECTED, S.IN_PROGRESS, S.COMPLETED, S.FAILED,
                            S.CANCELLED}),
    # Accepted subject to a condition (weather...): the provider still decides.
    S.PENDING_CONDITION: frozenset({S.ACCEPTED, S.REJECTED, S.CANCELLED, S.FAILED}),
    S.ACCEPTED: frozenset({S.IN_PROGRESS, S.COMPLETED, S.FAILED, S.CANCELLED}),
    S.IN_PROGRESS: frozenset({S.COMPLETED, S.FAILED, S.CANCELLED}),
}


class InvalidTransition(ValueError):
    pass


class ActionService:
    def __init__(self, session: Session, executors: ExecutorRegistry) -> None:
        self.s = session
        self.executors = executors

    # --------------------------------------------------------------- create
    def propose(
        self,
        *,
        property_id: str,
        action_type: str,
        summary: str,
        executor: str = "staff",
        stay_id: str | None = None,
        conversation_id: str | None = None,
        params: dict[str, Any] | None = None,
        urgency: Urgency | None = None,
    ) -> Action:
        action = Action(
            property_id=property_id,
            stay_id=stay_id,
            conversation_id=conversation_id,
            action_type=action_type,
            status=S.PROPOSED,
            urgency=urgency or default_urgency(action_type),
            summary=summary[:1000],
            params=params or {},
            result={},
            executor=executor,
        )
        self.s.add(action)
        self.s.flush()
        self._event(action, None, S.PROPOSED, "agent", None)
        return action

    # ---------------------------------------------------------------- submit
    def submit(self, action: Action, config: dict[str, Any] | None = None) -> Action:
        """Hand a PROPOSED action to its executor and record the outcome."""
        if action.status != S.PROPOSED:
            raise InvalidTransition(f"only proposed actions can be submitted (is {action.status.value})")
        executor = self.executors.get(action.executor)
        if executor is None:
            outcome = ExecutionOutcome(status=S.FAILED, error=f"unknown executor {action.executor!r}")
        else:
            try:
                outcome = executor.submit(action, config or {})
            except Exception as exc:  # an executor bug must not look like success
                outcome = ExecutionOutcome(status=S.FAILED, error=f"executor error: {type(exc).__name__}")
        actor = f"executor:{action.executor}"
        action.external_ref = outcome.external_ref or action.external_ref
        action.result = {**(action.result or {}), **outcome.result}
        if outcome.status == S.FAILED:
            action.error = outcome.error
            self.transition(action, S.FAILED, actor, outcome.error)
        else:
            self.transition(action, S.SUBMITTED, actor)
            if outcome.status in (S.ACCEPTED, S.REJECTED):
                self.transition(action, outcome.status, actor, outcome.result.get("message"))
        log_event("action_submitted", action_id=action.id, action_type=action.action_type,
                  executor=action.executor, status=action.status.value, error=outcome.error)
        return action

    # ------------------------------------------------------------ transition
    def transition(self, action: Action, to: ActionStatus, actor: str, detail: str | None = None) -> Action:
        frm = action.status
        if to not in ALLOWED_TRANSITIONS.get(frm, frozenset()):
            raise InvalidTransition(f"{frm.value} -> {to.value} is not allowed")
        action.status = to
        if to in TERMINAL_ACTION_STATUSES:
            action.closed_at = utcnow()
        if detail and actor.startswith("staff"):
            action.note = detail
        self._event(action, frm, to, actor, detail)
        log_event("action_transition", action_id=action.id, from_status=frm.value, to_status=to.value, actor=actor)
        return action

    def _event(self, action: Action, frm: ActionStatus | None, to: ActionStatus, actor: str, detail: str | None) -> None:
        seq = (self.s.scalar(select(func.max(ActionEvent.seq)).where(ActionEvent.action_id == action.id)) or 0) + 1
        self.s.add(ActionEvent(action_id=action.id, seq=seq, from_status=frm, to_status=to, actor=actor,
                               detail=(detail or None) and detail[:2000]))
        self.s.flush()
