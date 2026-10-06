# HOTELBOT: AI guest operations layer (prototype, iteration 3)

A WhatsApp-first guest-operations agent for any accommodation (hotels, hostels, resorts, vacation rentals, apartments, glamping). Hotel Aleksandar (Žabljak, Montenegro) will be the first real property; in the core it is just one `Property` with `property_type: hotel`.

> ⚠️ **All bundled property data is SYNTHETIC.** `data/hotel/example_hotel.yaml` ("Demo Mountain Hotel"), `data/properties/demo_apartment.yaml` ("Demo Lakeside Apartment") and `data/regions/zabljak_demo.yaml` (demo places, events, providers) are fictional. Nothing in them is a fact about Hotel Aleksandar. While they are loaded, every API reply carries `"knowledge_synthetic": true`.

## Product invariants

1. **Facts are grounded.** Property facts come from the property pack, or the bot says it cannot confirm them.
2. **Response authority.** LLMs may understand, phrase, summarise and classify. They may **not** decide that a real-world event happened. Every statement about a request ("sent", "accepted", "completed") is a fixed template chosen from the *stored* action state. Model prose that claims more is discarded. `SUBMITTED ≠ ACCEPTED ≠ COMPLETED`.
3. **Capabilities, not assumptions.** The bot only acts where the property declares a capability. Anything else is explained, never faked.
4. **Guest statements are not facts.** "We are 2 adults" is remembered on the stay as `confirmed: false`. Only staff or an integration sets authoritative stay data.
5. **Humans are first-class.** A handoff gives the conversation to staff; the bot stays silent except for emergencies.
6. **A quote is not a booking.** Third-party services go QUOTE → explicit CONSENT (to that offer) → TRANSACTION → provider confirmation. "ok" is not consent; "yes" with several open offers books nothing.
7. **Places come from data.** Restaurants, pharmacies, events, hours, distances and availability come only from structured region data or a provider - never from a model. Open/closed is computed; expired events never appear.

## Two worlds, four truths

```text
LOCAL WORLD (discovery)                 TRANSACTION WORLD (execution)
Place / Venue / Event / Infrastructure  Provider / Offering / Quote / Transaction
FIND - RECOMMEND - SAVE                 QUOTE - BOOK - ORDER - CHANGE - CANCEL
            \_________ app/marketplace/bridge.py (the only seam) _________/
```

A pharmacy is only a Place; a taxi is only a provider offering; a restaurant may be both. `tests/test_architecture.py` keeps the layers from importing each other.

| Truth | Source (never the LLM) |
|---|---|
| Knowledge ("breakfast time?") | property pack / place data |
| Availability ("table at 21:00?") | provider or inventory (capacity minus holds) |
| Transaction ("is the taxi booked?") | stored action state + signed provider callbacks |
| World ("open now?") | structured hours + clock + freshness |

## Domain

```text
Guest ──< Stay >── Property ──< KnowledgeDocument
            │          └── capabilities (actions / integrations / services), policy
            ├──< Conversation ──< Message
            │          └──< HumanHandoff
            └──< Action ──< ActionEvent   (PROPOSED → SUBMITTED → ACCEPTED → IN_PROGRESS → COMPLETED
                                           | REJECTED | FAILED | CANCELLED)
```

| Capability | How |
|---|---|
| Grounded answers | Lexical retrieval over the property pack. Answers are either extractive (no LLM) or LLM phrasing that must cite retrieved items and pass the authority guard. The bot handles multi-question messages, follow-ups ("is it free?") and conflicting knowledge. |
| Actions | `submit_action` checks the property's capability registry, then hands the action to its executor: the **staff queue**, or a **signed webhook** to an external partner. If the partner fails or times out, the action is marked FAILED and handed to staff with an honest message. |
| Status answers | "Is my late checkout confirmed?" is answered from stored action state. Staff transitions notify the guest with the template for the new state. |
| Stays | One stay per guest × property × visit. Guest-stated facts never leak across properties or visits. |
| Languages | English, Montenegrin Latin, Montenegrin/Serbian **Cyrillic → Cyrillic replies**, and Russian (routing, safety, handoff and operational templates; English-only knowledge is flagged as such). |
| Handoff | Triggers: emergency, human request, complaint, billing dispute, booking change, repeated failure. The failure threshold is a per-property, per-intent policy. |
| WhatsApp | Meta Cloud API webhook: verification, HMAC, idempotent on `wamid`. Routes to a property by the receiving phone number. A mock transport is included. |
| LLM | `none` / `openai` (any OpenAI-compatible API) / `anthropic` |
| Persistence | PostgreSQL (or SQLite). Alembic migrations run automatically on startup; Core v1 databases are upgraded in place. |
| Transactions | Quotes, explicit scoped consent, provider adapters (mock, signed webhook), idempotent submission via a PostgreSQL job queue with retries/backoff, signed replay-protected callbacks, honest failure + staff handoff. See [`docs/PROVIDER_INTEGRATION.md`](docs/PROVIDER_INTEGRATION.md). |
| Marketplace | Transport (taxi / airport / intercity), rentals (skis, snowboards, bikes, e-bikes, cars, gear) and guides/tours on one model: provider discovery by fit + availability + price + property relationships, inventory holds, material terms before consent, weather-conditional bookings, changes as replacement offers, policy-checked cancellation, multi-service requests with per-item consent. See [`docs/MARKETPLACE.md`](docs/MARKETPLACE.md). |
| Local travel | Region packs of places / events / offerings / inventory; discovery ("open now", "still serving at 22:30", "after midnight", vegan, wheelchair, pets, lively); save / book from results; trip plan with per-item status; multi-part trip requests. See [`docs/LOCAL_TRAVEL.md`](docs/LOCAL_TRAVEL.md). |
| Evaluation | 154 deterministic product scenarios; invariant scenarios (98) are gates (see below). |

## Quick start

```bash
cd hotelbot
pip install -r requirements-dev.txt
pytest                                      # 582 tests (SQLite)
python -m evals                             # product evaluation summary
uvicorn app.main:create_app --factory --reload
```

```bash
curl -s localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"guest_id":"demo-001","message":"Can I stay until 3pm?"}'
curl -s localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"guest_id":"demo-001","message":"Book me a taxi to the airport","property":"demo-apartment"}'
python -m app.cli --verbose --property demo-apartment
```

Flagship multi-part trip (synthetic Žabljak region data; skis are in season from mid-December, so try it with a January date in mind):

```bash
curl -s localhost:8000/api/chat -H 'content-type: application/json' -d '{"guest_id":"trip-1","message":
  "We arrive Friday at 8pm, there are four of us. Get us a transfer from Podgorica. Find somewhere local for dinner that is still serving when we arrive. We want skis Saturday morning. Find a Russian-speaking guide for Sunday. And suggest somewhere lively for drinks Saturday night."}'
curl -s localhost:8000/api/chat -H 'content-type: application/json' -d '{"guest_id":"trip-1","message":"book the transfer"}'
```

Docker Compose (PostgreSQL + both demo properties): `cp .env.example .env && docker compose up --build`.

LLM: set `HOTELBOT_LLM_PROVIDER=anthropic|openai`, `HOTELBOT_LLM_MODEL` and the matching API key. If the LLM fails, the bot falls back to rules and extractive answers.

## Property packs

A pack is one YAML file per property, with no code changes needed:

```yaml
pack:
  property_slug: example-hotel
  property_name: "Demo Mountain Hotel (synthetic data)"
  property_type: hotel            # hotel | hostel | resort | vacation_rental | apartment | glamping | other
  timezone: Europe/Podgorica
  default_language: en
  emergency_number: "112"         # not hard-coded in the core
  whatsapp_phone_number_id: ...   # optional: route this WhatsApp number to this property
  source: "..."
  synthetic: false

capabilities:
  actions:
    late_arrival_request: {executor: staff}
    transport_booking:    {executor: webhook, config: {url: "https://partner/...", secret_env: PARTNER_SECRET}}
  integrations: [human_staff]
  external_services: [transport, activities]

policy:
  max_consecutive_failures: 2
  failure_thresholds: {LOCAL_RECOMMENDATION: 1}

items:
  - key: breakfast
    category: dining
    topic: breakfast               # items sharing a topic must agree (conflicts are refused)
    content: {en: "...", cnr: "..."}          # cnr-Cyrl is derived; add ru if available
    keywords: {en: [...], cnr: [...], ru: [...]}
    metadata: {requires_staff_approval: [late_arrival_request]}
```

Action types: `late_arrival_request`, `early_checkin_request`, `late_checkout_request`, `housekeeping_request`, `maintenance_request`, `restaurant_booking`, `transport_booking`, `booking_inquiry`, `staff_question`.

Packs listed in `HOTELBOT_KNOWLEDGE_PATH` + `HOTELBOT_EXTRA_PACK_PATHS` are ingested on startup (idempotent). Core v1 packs (`hotel_slug`, no capabilities) still load, with all actions handled by staff.

## API

| Method & path | Purpose |
|---|---|
| `POST /api/chat` `{guest_id, message, property?}` | demo channel, same orchestrator as WhatsApp |
| `GET /api/dev/outbox?to=` | messages sent by the mock transport |
| `GET/POST /webhooks/whatsapp` | Meta verification / inbound messages |
| `GET /api/staff/properties` | properties with capability summary |
| `GET /api/staff/actions[?action_status=&property=]`, `GET /api/staff/actions/{id}` | action queue, with audit events |
| `POST /api/staff/actions/{id}/transition` `{to, note?, staff_name?, notify_guest=true}` | lifecycle change. Invalid moves return 409. The guest gets the template for the new state. |
| `GET /api/staff/stays[?stay_status=&property=]`, `GET/PATCH /api/staff/stays/{id}` | stays; PATCH records staff-verified booking data |
| `GET /api/staff/conversations`, `GET /api/staff/conversations/{id}`, `POST .../{id}/messages` | conversations, staff replies |
| `GET /api/staff/handoffs`, `POST .../{id}/accept`, `POST .../{id}/resolve` | handoffs |
| `GET /api/staff/requests`, `POST /api/staff/requests/{id}/resolve` | Core v1 compatibility view of actions |
| `GET /api/staff/quotes`, `GET /api/staff/transactions`, `GET /api/staff/jobs[?job_status=]` | offers, provider transactions, job queue |
| `GET /api/staff/stays/{id}/plan` | the guest's trip plan with per-item status |
| `POST /api/providers/{property}/{provider}/callbacks` | signed provider callbacks (HMAC, 300 s replay window, idempotent on `event_id`) |

Staff endpoints require `X-Staff-Token` (they are open without one only when `HOTELBOT_ENV=dev`).

## Evaluation

```bash
python -m evals                                   # TOTAL / PASS / FAIL by category + failure details
python -m evals --category authority
python -m evals --markdown docs/EVAL_REPORT.md --json eval.json
```

Scenarios live in `evals/scenarios/*.yaml` (grounding, actions, authority, handoff, safety, memory, conversation, languages, transactions, local, marketplace). Each one runs through a fresh real container: real packs plus scenario edits, an optional scripted LLM, and an optional mocked partner endpoint. Every bot message is also checked against the authority invariant. `gate: true` scenarios are product invariants: they run in pytest, and the CLI exits 1 if one fails. Latest report: [`docs/EVAL_REPORT.md`](docs/EVAL_REPORT.md). Retrieval findings: [`docs/RETRIEVAL_ANALYSIS.md`](docs/RETRIEVAL_ANALYSIS.md).

## Migrations

New databases are created from the models and stamped. Existing databases are upgraded with Alembic on startup (`HOTELBOT_AUTO_MIGRATE=true`); Core v1 databases (no version table) are detected and upgraded. Manual alternative: `HOTELBOT_AUTO_MIGRATE=false alembic upgrade head`. `0002_stay_engine` is forward-only; `0003_transactions`, `0004_local_travel` and `0005_marketplace` are additive. **Back up first** - see [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for backup, restore and worker operations.

## Testing

```bash
pytest
HOTELBOT_TEST_DATABASE_URL=postgresql+psycopg://hotelbot:hotelbot@localhost:5432/hotelbot_test pytest
```

## Layout

```text
app/
  agent/        orchestrator, intents, language, memory, policies, handoff, responder, authority,
                dialogue, runtime, prompts, messages
  actions/      catalog, service (state machine), executors (staff, webhook), staff_ops
  capabilities/ registry
  stays/        service
  knowledge/    schemas (pack format), ingest, service (retriever)
  transactions/ catalog, slots, consent, service, dialogue, callbacks, format, providers/ (mock, webhook)
  marketplace/  discovery (provider candidates), inventory (holds), pricing, terms, bridge (the seam to places/events)
  nlp/          temporal (days, times, counts - shared by both worlds)
  jobs/         queue (outbox, SKIP LOCKED, retries), handlers
  places/       taxonomy, hours, geo, freshness, availability, pack (region ingest)
  discovery/    nlu, engine, render
  trip/         itinerary, concierge (discovery, save/book, plan, multi-part trips)
  llm/  tools/  db/  whatsapp/  api/  schemas/   worker.py, clock.py
migrations/     Alembic (0001_core_v1 ... 0006_layers)
evals/          harness, report, scenarios/
data/           hotel/example_hotel.yaml, properties/demo_apartment.yaml, regions/zabljak_demo.yaml   (SYNTHETIC)
docs/           DECISIONS.md, EVAL_REPORT.md, RETRIEVAL_ANALYSIS.md, PROVIDER_INTEGRATION.md, OPERATIONS.md, LOCAL_TRAVEL.md, MARKETPLACE.md
```
