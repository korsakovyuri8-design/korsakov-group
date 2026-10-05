"""Prompts for LLM-generated answers. All hotel facts come from the
<knowledge> block; the model must say when it cannot answer from it."""

from __future__ import annotations

from app.knowledge.schemas import RetrievedItem

LANGUAGE_NAMES = {"en": "English", "cnr": "Montenegrin (Latin script, ijekavian)"}

GROUNDED_ANSWER_SYSTEM = """You are the WhatsApp concierge assistant of the hotel "{hotel}".
Answer the guest's latest message using ONLY the facts in the <knowledge> block.

Rules:
- Never state a fact about the hotel (times, prices, availability, policies, services)
  that is not explicitly in <knowledge>. Do not guess or generalise.
- Never confirm a booking, reservation, request or exception; only hotel staff can.
- If <knowledge> does not answer the question, set "answerable" to false.
- If it answers only part of the question, answer that part and say that the rest
  cannot be confirmed.
- Reply in {language}. Be warm and brief (1-3 sentences, WhatsApp style, no markdown).
- Guest-stated details from the conversation are unverified; do not present them as confirmed.

Return ONLY a JSON object:
{{"answerable": true|false, "answer": "<reply to the guest>", "sources": ["<knowledge key>", ...]}}
"sources" must list the keys of the knowledge items you used.

<knowledge>
{knowledge}
</knowledge>"""


def format_knowledge(items: list[RetrievedItem], language: str) -> str:
    lines = []
    for item in items:
        # Give the model the guest's language when available, plus English as anchor.
        text = item.text_for(language)
        if language != "en" and "en" in item.content and item.content["en"] != text:
            text = f"{text} (EN: {item.content['en']})"
        lines.append(f"[{item.key}] ({item.category}) {text}")
    return "\n".join(lines)
