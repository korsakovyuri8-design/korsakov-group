"""Terminal demo: chat with the bot through the same Orchestrator the
WhatsApp webhook uses.

    python -m app.cli                    # interactive
    python -m app.cli --guest demo-002   # pick a guest id
    echo "What time is breakfast?" | python -m app.cli
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.container import build_container
from app.observability import configure_logging
from app.schemas.messages import InboundMessage


def main() -> int:
    parser = argparse.ArgumentParser(description="HOTELBOT terminal demo")
    parser.add_argument("--guest", default="cli-guest")
    parser.add_argument("--verbose", action="store_true", help="print intent/grounding details and logs")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("INFO" if args.verbose else "ERROR")
    container = build_container(settings)
    interactive = sys.stdin.isatty()
    if interactive:
        print(f"HOTELBOT demo - guest '{args.guest}'. Ctrl-D to quit.\n")
    for line in sys.stdin if not interactive else iter(lambda: input("you> "), None):
        text = line.strip()
        if not text:
            continue
        reply = container.orchestrator.handle(InboundMessage(channel="demo", sender_id=args.guest, text=text))
        if not interactive:
            print(f"you> {text}")
        print(f"bot> {reply.text if reply.text else '(silent - conversation is with hotel staff)'}")
        if args.verbose:
            print(f"     [intent={reply.intent} lang={reply.language} grounded={reply.grounded} "
                  f"sources={reply.sources} actions={[a.kind for a in reply.actions]} handed_off={reply.handed_off}]")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
