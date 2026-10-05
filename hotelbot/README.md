# HOTELBOT: AI guest operations agent (prototype, iteration 1)

A WhatsApp-first concierge and guest-operations agent, built first for Hotel Aleksandar (Žabljak, Montenegro) and designed as a reusable hospitality product.

> ⚠️ **The bundled hotel data is SYNTHETIC.** `data/hotel/example_hotel.yaml` describes a fictional "Demo Mountain Hotel". None of it is a fact about Hotel Aleksandar. Replace it with the official data (see [Replacing the knowledge pack](#replacing-the-knowledge-pack)). While it is loaded, every API reply carries `"knowledge_synthetic": true`.

## What it does (iteration 1)

| Capability | How |
|---|---|
| Grounded hotel answers | Retrieval over a hotel knowledge pack. The answer is the pack text (no LLM), or LLM phrasing that must cite retrieved items. Otherwise the bot says "I can't confirm that, shall I ask staff?" |
| English + Montenegrin | Automatic detection (Latin/Cyrillic, with or without diacritics). The bot replies in the guest's language. |
| Actions | Service and booking requests become `HotelRequest`s through the `create_hotel_request` tool. The bot never confirms them; staff do. |
| Human handoff | Triggered by emergency, explicit request, complaint, billing dispute, booking change or repeated failure. Staff get a structured package, and the bot goes silent until staff hand the conversation back. |
| Session memory | Arrival/departure dates, guest count and room number, stored as *unconfirmed guest statements* |
| WhatsApp | Meta Cloud API webhook (verification, HMAC signature, idempotent on `wamid`), plus a mock transport for local work |
| LLM providers | `none` / `openai` (any OpenAI-compatible API) / `anthropic`, selected by env |
| Staff API | Conversations, request queue, handoffs, staff replies |
| Observability | JSON-lines events: `message_received`, `intent_detected`, `knowledge_retrieved`, `tool_called`, `handoff_created`, `message_sent`, `error` |

## Quick start

### Local (no Docker, SQLite, no LLM)

```bash
cd hotelbot
pip install -r requirements-dev.txt
pytest                                    # 159 tests
uvicorn app.main:create_app --factory --reload
```

```bash
curl -s localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"guest_id":"demo-001","message":"What time is breakfast?"}'
```

Or chat in the terminal (same orchestrator):

```bash
python -m app.cli --verbose
```

### Docker Compose (PostgreSQL)

```bash
cd hotelbot
cp .env.example .env        # optional: set LLM / WhatsApp / staff token
docker compose up --build
```

### Enable an LLM

```bash
# Anthropic
HOTELBOT_LLM_PROVIDER=anthropic HOTELBOT_LLM_MODEL=claude-sonnet-5-5 HOTELBOT_ANTHROPIC_API_KEY=...
# OpenAI or any OpenAI-compatible server (vLLM, Ollama, OpenRouter...)
HOTELBOT_LLM_PROVIDER=openai HOTELBOT_LLM_MODEL=<model> HOTELBOT_OPENAI_API_KEY=... HOTELBOT_OPENAI_BASE_URL=...
```

If the LLM fails or times out, the bot falls back to rules and extractive answers. It does not error.

## Demo script

```text
guest> What time is breakfast?                      -> grounded answer (EN)
guest> Kada je doručak?                             -> grounded answer (CNR)
guest> Can we check in after 11pm? We arrive on 20 December, 2 adults.
                                                     -> quotes late-arrival policy, creates LATE_CHECK_IN request,
                                                        "not confirmed yet"; memory: arrival 12-20, 2 guests (unconfirmed)
guest> Do you have a sauna?                         -> "can't confirm... shall I ask staff?"
guest> yes                                          -> question forwarded to the staff queue
guest> The room is dirty and nobody answers!        -> complaint handoff (high urgency); bot goes silent
staff  POST /api/staff/conversations/{id}/messages  -> staff reply delivered to the guest
staff  POST /api/staff/handoffs/{id}/resolve        -> bot resumes
```

## API

| Method & path | Purpose |
|---|---|
| `GET /health` | liveness |
| `POST /api/chat` `{guest_id, message}` | demo channel, same orchestrator as WhatsApp |
| `GET /api/dev/outbox?to=` | messages sent by the mock transport / to demo guests |
| `GET /webhooks/whatsapp` | Meta verification (`hub.mode`, `hub.verify_token`, `hub.challenge`) |
| `POST /webhooks/whatsapp` | inbound messages (requires `X-Hub-Signature-256` when the app secret is set) |
| `GET /api/staff/conversations[?conv_status=]` | list conversations |
| `GET /api/staff/conversations/{id}` | messages + memory |
| `POST /api/staff/conversations/{id}/messages` `{text, staff_name?}` | staff → guest (via the guest's channel) |
| `GET /api/staff/requests[?request_status=pending]` | request queue |
| `POST /api/staff/requests/{id}/resolve` `{status?, note?, reply_to_guest?}` | resolve/reject, optionally reply |
| `GET /api/staff/handoffs[?handoff_status=]` | handoffs with packages |
| `POST /api/staff/handoffs/{id}/accept` | staff takes it |
| `POST /api/staff/handoffs/{id}/resolve` `{close_conversation?}` | hand back to the bot (or close) |

Staff endpoints require the `X-Staff-Token` header. They are open without a token only when `HOTELBOT_ENV=dev`. Interactive docs are at `/docs`.

## Architecture

```text
WhatsApp webhook ─┐                          ┌─ knowledge/ (pack → DB → retriever)
/api/chat (demo) ─┼─► agent/orchestrator ────┼─ agent/intents (rules + optional LLM)
CLI ──────────────┘    one path, one txn     ├─ agent/responder (grounded answer)
                                             ├─ agent/policies + handoff (escalation)
                                             ├─ agent/memory (guest-stated facts)
                                             ├─ tools/ (create_hotel_request, registry)
                                             └─ llm/ (none | openai | anthropic)
Reply ─► whatsapp/{meta,mock} transport      db/ (SQLAlchemy models + repositories)
```

```text
app/
  main.py, container.py, config.py, observability.py, text.py, cli.py
  api/        chat.py  whatsapp.py  staff.py  deps.py
  agent/      orchestrator.py intents.py language.py memory.py policies.py handoff.py
              responder.py prompts.py messages.py
  knowledge/  schemas.py ingest.py service.py
  llm/        base.py openai_provider.py anthropic_provider.py factory.py
  tools/      registry.py hotel_request.py
  db/         models.py session.py repositories.py
  whatsapp/   base.py meta.py mock.py
  schemas/    messages.py conversations.py
data/hotel/example_hotel.yaml     ← SYNTHETIC
docs/DECISIONS.md                 ← architecture decisions (read this)
tests/
```

Every turn follows the same sequence: dedupe → detect language → store message → update memory → classify intent. If a human owns the conversation, the bot stays silent unless the message is an emergency. Otherwise it resolves a pending yes/no offer, then routes the message. A route ends in a handoff, a request, a grounded answer or "cannot confirm", or a clarification. The reply is then stored and sent.

## Replacing the knowledge pack

1. Create `data/hotel/<hotel>.yaml` in the same format as the example. Set `pack.synthetic: false`. Fill per-language `content` (`en`, `cnr`) and `keywords` (the words guests actually use). For items that need staff authorisation, set `metadata.requires_staff_approval: [late_check_in, ...]`.
2. Set `HOTELBOT_HOTEL_SLUG` and `HOTELBOT_KNOWLEDGE_PATH`.
3. Restart, or run `python -m app.knowledge.ingest data/hotel/<hotel>.yaml`. Ingestion is idempotent and removes items that are no longer in the file.

No code changes are needed.

## Testing

```bash
pytest                                       # in-memory SQLite
HOTELBOT_TEST_DATABASE_URL=postgresql+psycopg://hotelbot:hotelbot@localhost:5432/hotelbot_test pytest
```

The tests exercise real behaviour: the orchestrator with the real knowledge pack, HTTP endpoints, HMAC signatures, and provider wire formats through `httpx.MockTransport`. The only test double is `ScriptedLLM`, which implements the provider interface with queued responses.

See `docs/DECISIONS.md` for the reasoning behind each design choice.
