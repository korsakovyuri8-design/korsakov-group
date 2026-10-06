"""What the guest wants to DO, independent of the domain.

    DISCOVERY operations (local world)      EXECUTION operations (transaction world)
    FIND       "where is a pharmacy"        QUOTE   "how much is a taxi to..."
    RECOMMEND  "suggest a lively bar"       BOOK    "book the transfer"
    SAVE       "save 2"                     ORDER   "order / buy tickets"
                                            CHANGE  "move my taxi to 7am"
                                            CANCEL  "cancel the guide"

Deterministic (en / cnr / ru). Used for routing decisions and logged on
every turn, so we can see where the two worlds meet.
"""

from __future__ import annotations

import enum

from app.text import contains_phrase, fold


class Operation(str, enum.Enum):
    FIND = "find"
    RECOMMEND = "recommend"
    SAVE = "save"
    QUOTE = "quote"
    BOOK = "book"
    ORDER = "order"
    CHANGE = "change"
    CANCEL = "cancel"


DISCOVERY_OPERATIONS = frozenset({Operation.FIND, Operation.RECOMMEND, Operation.SAVE})
EXECUTION_OPERATIONS = frozenset({Operation.QUOTE, Operation.BOOK, Operation.ORDER, Operation.CHANGE,
                                  Operation.CANCEL})

_WORDS: dict[Operation, list[str]] = {
    Operation.FIND: ["where is", "where are", "where can", "find", "nearest", "open now", "is there", "nadji",
                     "gdje", "najbliz*", "где", "найди*", "ближайш*"],
    Operation.RECOMMEND: ["recommend*", "suggest*", "any good", "best", "preporu*", "посоветуй*", "порекоменд*"],
    Operation.SAVE: ["save", "keep", "remember", "sacuvaj", "zapamti", "сохрани", "запомни"],
    Operation.QUOTE: ["how much", "price", "cost", "koliko kosta", "cijena", "сколько стоит", "цена"],
    Operation.BOOK: ["book", "reserve", "rezervis*", "забронир*", "бронир*", "закаж*", "need a taxi", "get us",
                     "rent", "hire", "iznajm*", "прокат*", "аренд*"],
    Operation.ORDER: ["order", "buy", "tickets", "naruci", "kupi", "закажи доставку", "купи*", "билет*"],
    Operation.CHANGE: ["change", "move", "reschedule", "instead", "promijen*", "pomjeri", "измени*", "перенес*"],
    Operation.CANCEL: ["cancel*", "otkaz*", "отмен*"],
}


def classify_operations(text: str) -> set[Operation]:
    folded = fold(text)
    return {op for op, words in _WORDS.items() if any(contains_phrase(folded, w) for w in words)}


def layer(ops: set[Operation]) -> str:
    """'discovery', 'execution', 'both' or 'none'."""
    d, e = bool(ops & DISCOVERY_OPERATIONS), bool(ops & EXECUTION_OPERATIONS)
    return "both" if d and e else "discovery" if d else "execution" if e else "none"
