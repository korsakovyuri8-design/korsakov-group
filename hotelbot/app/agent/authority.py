"""Response Authority invariant.

    LLMs may understand, phrase, summarise and classify.
    They may NOT decide whether a real-world event happened.

1. Statements about actions are produced only by `status_message()`, which
   maps the *stored* ActionStatus to a fixed template. Each template claims
   exactly its own status (tests/test_authority.py checks this for every
   locale).
2. Any model-written prose is screened by `unbacked_claims()`: phrases that
   assert acceptance/booking/confirmation or completion are rejected unless
   the system holds an action in a state that backs them. The caller then
   falls back to property-authored text.

Claim detection works on folded text (en / cnr / ru) and errs on the side
of flagging: a false positive only costs nicer phrasing.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable

from app.actions.catalog import action_label
from app.agent import messages as msg
from app.db.models import Action, ActionStatus
from app.text import fold


class ClaimLevel(str, enum.Enum):
    ACCEPTED = "accepted"      # confirmed / approved / booked / on its way
    COMPLETED = "completed"    # done / fixed / delivered


STATUS_TEMPLATE: dict[ActionStatus, str] = {
    ActionStatus.PROPOSED: "offer_request",
    ActionStatus.SUBMITTED: "action_submitted_named",
    ActionStatus.ACCEPTED: "action_accepted",
    ActionStatus.IN_PROGRESS: "action_in_progress",
    ActionStatus.COMPLETED: "action_completed",
    ActionStatus.REJECTED: "action_rejected",
    ActionStatus.FAILED: "action_failed",
    ActionStatus.CANCELLED: "action_cancelled",
}

# Which stored statuses back which claim level.
BACKING: dict[ClaimLevel, frozenset[ActionStatus]] = {
    ClaimLevel.ACCEPTED: frozenset({ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS, ActionStatus.COMPLETED}),
    ClaimLevel.COMPLETED: frozenset({ActionStatus.COMPLETED}),
}


def status_message(action: Action, locale: str, property_name: str) -> str:
    """The only sanctioned guest-facing sentence about an action's state."""
    return msg.t(STATUS_TEMPLATE[action.status], locale,
                 action=action_label(action.action_type, locale), property=property_name)


_NEG = re.compile(r"(\bnot|n't|\bnever|\bnothing|\bne|\bnije|\bnisu|\bnijesu|\bnece|\bnista|\bnicego|\bnicto)"
                  r"\s+(\w+\s+){0,2}$")

_CLAIMS: dict[ClaimLevel, list[re.Pattern[str]]] = {
    ClaimLevel.ACCEPTED: [
        # en
        re.compile(r"\b(is|are|was|were|been|it's|its|all|now)\s+(now\s+|all\s+)?(confirmed|approved|accepted|booked|"
                   r"reserved|arranged|guaranteed|scheduled|sorted|set)\b"),
        re.compile(r"\b(i|we)(\s+have|'ve|ve)?\s+(booked|reserved|confirmed|approved|arranged|scheduled|ordered|"
                   r"upgraded|extended)\b"),
        re.compile(r"\byou\s+(can|may)\s+(now\s+)?(stay|keep\s+the\s+room)\s+(until|till|longer)\b"),
        re.compile(r"\bon\s+(its|their|his|her)\s+way\b"),
        # cnr (folded)
        re.compile(r"\b(je|su|bice|ce\s+biti)\s+(vec\s+|sada\s+)?(potvrdjen\w*|odobren\w*|prihvacen\w*|rezervisan\w*|"
                   r"zakazan\w*|organizovan\w*)"),
        re.compile(r"\b(rezervisa|potvrdi|odobri|zakaza|organizova)(o|la|li)\s+(sam|smo)\b"),
        re.compile(r"\b(sam|smo)\s+(vam\s+)?(rezervisa|potvrdi|odobri|zakaza|organizova)\w*"),
        re.compile(r"\bmozete\s+ostati\s+do\b"),
        # ru (folded)
        re.compile(r"\b(podtverzden\w*|zabronirovan\w*|odobren\w*|prinyat\w*|soglasovan\w*|zarezervirovan\w*)"),
        re.compile(r"\b(zabronirova|podtverdi|odobri|zarezervirova)l\w*"),
        re.compile(r"\bmozete\s+ostatsya\s+do\b"),
    ],
    ClaimLevel.COMPLETED: [
        re.compile(r"\b(is|are|was|were|been|it's|its|all)\s+(now\s+)?(completed|done|delivered|fixed|repaired|"
                   r"cleaned|resolved|finished)\b"),
        re.compile(r"\b(i|we)(\s+have|'ve|ve)?\s+(completed|fixed|delivered|cleaned|repaired)\b"),
        re.compile(r"\b(je|su)\s+(vec\s+|sada\s+)?(izvrsen\w*|zavrsen\w*|popravljen\w*|ociscen\w*|dostavljen\w*)"),
        re.compile(r"\b(vypolnen\w*|zaversen\w*|gotovo|pocinen\w*|ubran\w*|dostavlen\w*)"),
    ],
}


def detect_claims(text: str) -> set[ClaimLevel]:
    """Claim levels asserted (non-negated) in `text`."""
    folded = fold(text)
    found: set[ClaimLevel] = set()
    for level, patterns in _CLAIMS.items():
        for pattern in patterns:
            for m in pattern.finditer(folded):
                if not _NEG.search(folded[max(0, m.start() - 25):m.start()]):
                    found.add(level)
    return found


def unbacked_claims(text: str, statuses: Iterable[ActionStatus]) -> set[ClaimLevel]:
    """Claim levels in `text` that no stored action status backs."""
    have = set(statuses)
    return {level for level in detect_claims(text) if not (BACKING[level] & have)}
