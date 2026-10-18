"""What a visitor pastes, made safe and recognised - before anything is stored.

* a size cap per document;
* HTML is reduced to plain text: tags, scripts, styles, images and links are
  dropped, so nothing in a document can make the browser or the server
  fetch anything;
* a body pasted without mail headers gets a neutral header block, because
  the parsers read the body after the first blank line;
* the format is recognised ONLY by Atlas's deterministic parsers. An unknown
  format is answered as unknown - it is never stored, never sent to a model.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from app.journey import ingest

MAX_DOCUMENT_BYTES = 150 * 1024
BOOKING_KINDS = ("airline_email", "hotel_email", "restaurant_email", "transfer_email")   # never guest chat
_HEADER = re.compile(r"^(Message-ID|From|To|Subject|Date|Received|Return-Path|MIME-Version|Content-Type):", re.I)
_LOOKS_HTML = re.compile(r"<\s*(html|body|div|p|br|table|tr|td|span|h[1-6]|li)\b", re.I)


class TooLarge(ValueError):
    pass


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article"}
    SKIP = {"script", "style", "head", "title", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)


def to_text(text: str) -> str:
    """Plain text; HTML (if any) reduced to its readable lines."""
    if _LOOKS_HTML.search(text):
        p = _Text()
        p.feed(text)
        p.close()
        text = html.unescape("".join(p.out))
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def prepare(text: str) -> str:
    """The document as Atlas stores it. Raises TooLarge."""
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise TooLarge
    clean = to_text(text)
    first = next((line for line in clean.split("\n") if line.strip()), "")
    if not _HEADER.match(first):                 # a pasted body: the parsers read after the header block
        clean = "X-Atlas-Import: pasted\n\n" + clean
    return clean


def recognise(raw: str) -> str | None:
    """A supported booking confirmation or a conservative stated trip plan."""
    for kind in BOOKING_KINDS:
        try:
            if ingest.PARSERS[kind][1](raw) is not None:
                return kind
        except Exception:                        # a parser that cannot read it does not recognise it
            continue
    # Free text may be useful without being a booking.  Keep this after all
    # provider parsers so a real confirmation always wins.
    try:
        if ingest.PARSERS["plan_statement"][1](raw) is not None:
            return "plan_statement"
    except Exception:
        pass
    return None
