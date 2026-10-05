# HOTELBOT - Architecture Decisions

Format: Decision / Reason / Alternatives considered / Consequences.
Newest at the bottom. Iteration 1 decisions are D-001 to D-016.

---

## D-001 - Self-contained `hotelbot/` directory in the korsakov-group repository

**Decision.** HOTELBOT lives in `hotelbot/` inside the existing `korsakov-group` repository (which hosts the static company website).

**Reason.** This is the only repository available to the session. A subdirectory keeps the website untouched and lets the project move to its own repo later with `git subtree split` or a plain copy.

**Alternatives.** A new repository (not available from this session); putting code at the repo root (would mix with the website).

**Consequences.** All commands run from `hotelbot/`. Moving to a dedicated repo later costs nothing.

---

## D-002 - Synchronous FastAPI + SQLAlchemy 2.0, one transaction per inbound message

**Decision.** Endpoints and the orchestrator are synchronous (`def`). FastAPI runs them in its threadpool. `Orchestrator.handle()` opens one DB session and commits once per guest message.

**Reason.** Per-message work is a short sequence of DB writes plus at most two LLM calls. Sync code is simpler to read, test and debug. One transaction per message means a failure never leaves half a turn persisted, such as a request created with no bot reply.

**Alternatives.** Full async (asyncpg, AsyncSession, async httpx). It would give better throughput at high concurrency, at the cost of more complexity and harder testing.

**Consequences.** The throughput ceiling is the threadpool size, which is far above a single hotel's WhatsApp volume. Switching to async later is mechanical because I/O sits behind small interfaces.

---

## D-003 - PostgreSQL target; SQLite allowed; `create_all` instead of migrations for now

**Decision.** Docker Compose runs PostgreSQL 16 (the pgvector image). The models use only portable types (JSON, string-backed enums), so SQLite works for tests and quick local runs. Tables are created with `metadata.create_all()` at startup.

**Reason.** Tests stay fast and need no services. The whole suite has also been run against real PostgreSQL (`HOTELBOT_TEST_DATABASE_URL`). Enums are stored as strings, not native PG enums, so adding a request type needs no `ALTER TYPE`.

**Alternatives.** Postgres-only (JSONB, native enums) with Alembic from day one.

**Consequences.** **Alembic must be added before the first deployment that has data worth keeping.** JSON columns are not JSONB-indexed yet, which does not matter at current scale.

---

## D-004 - Hotel knowledge = a versioned data file ("knowledge pack"), ingested into the DB

**Decision.** All hotel facts live in a YAML/JSON knowledge pack (`data/hotel/*.yaml`), validated by Pydantic (`app/knowledge/schemas.py`). Each item has a `key`, a `category`, per-language `content`, optional per-language `keywords`, a `source` and `metadata`. Ingestion is an idempotent upsert keyed by `(hotel, key)` and uses a content hash. Items removed from the pack are deleted, so the file is the source of truth. Ingestion runs at startup and through `python -m app.knowledge.ingest`.

**Reason.** The real "Informacije o Hotelu" document has not arrived. The real data must replace the synthetic pack with zero code change. Per-language content lets the bot quote facts verbatim in Montenegrin without machine translation.

**Alternatives.** Free-text Markdown chunks; facts in code; an admin UI.

**Consequences.** The official document has to be converted into the pack format, which needs a human pass for accuracy. A Markdown/PDF ingestion helper is a future slice. The synthetic pack is labelled synthetic at pack level and on every item, and the API returns `knowledge_synthetic: true` while it is loaded.

---

## D-005 - Lexical coverage retriever now, behind a replaceable `KnowledgeRetriever` interface

**Decision.** Retrieval is in-memory and IDF-weighted, scored by query-term coverage over diacritic-folded, crudely stemmed terms from content plus curated keywords. The score is in [0, 1] and doubles as the grounding signal (`HOTELBOT_GROUNDING_MIN_SCORE`, default 0.5).

**Reason.** A single hotel has tens of items. Coverage gives an interpretable "how much of the question does this item explain" score, so "Do you have a sauna?" scores 0 and is not grounded. Embeddings return a nearest neighbour for every query, so they need a carefully calibrated threshold to say "nothing matches". With no dependency and no API calls, the bot works offline and the tests are deterministic.

**Alternatives.** pgvector embeddings (still planned: the Compose image already ships pgvector); BM25; asking an LLM to pick items.

**Consequences.** Recall depends on the curated `keywords`, so whoever converts the real data should add the synonyms guests use. Paraphrases with no shared vocabulary will miss. Mitigations: the LLM grounding check (D-007), "cannot confirm" with an offer to ask staff, and escalation after repeated failures. Next step: a hybrid retriever (lexical + embeddings) behind the same Protocol.

---

## D-006 - The bot works without an LLM ("extractive mode"); the LLM is an enhancement

**Decision.** `HOTELBOT_LLM_PROVIDER=none` is a fully supported mode. Grounded answers are the retrieved pack text in the guest's language. Intents come from rules, and operational replies come from templates.

**Reason.** It is reliable for a live demo (no network or quota failures), it is cheap, and it cannot hallucinate. It also lets every test run without credentials.

**Alternatives.** Requiring an LLM.

**Consequences.** Extractive answers read less naturally ("Breakfast is served ... 07:30 to 10:00 ...") and cannot combine facts. With an LLM configured, phrasing improves while grounding guarantees stay in place (D-007).

---

## D-007 - Grounding contract for LLM answers

**Decision.** The LLM only sees the retrieved items, and only when retrieval is grounded. It must return `{"answerable", "answer", "sources"}`. An answer is accepted only if it is answerable, non-empty, and cites one or more retrieved keys and *only* retrieved keys. If the model declines or cites anything unknown, the guest gets "I can't confirm that... shall I ask staff?". If the provider fails (timeout, HTTP error, unparseable output), the bot falls back to the extractive answer, because the retrieved knowledge is still valid.

**Reason.** This puts "hotel facts must be grounded" into code rather than relying on a prompt alone.

**Alternatives.** Free-form generation with the prompt only; post-hoc fact checking by a second LLM call.

**Consequences.** Citation checking is structural: it cannot detect a model that cites the right key but misquotes it. A later evaluation harness (ChatGPT's track) should measure faithfulness.

---

## D-008 - Operational messages are fixed templates, never LLM output

**Decision.** Handoff notices, request confirmations, emergency instructions, clarifications and "cannot confirm" messages come from `app/agent/messages.py` (en + cnr).

**Reason.** These sentences commit the hotel to something ("sent to staff", "not confirmed yet", "call 112"). They must be exact, reviewable and translated by a human.

**Alternatives.** LLM-generated replies everywhere.

**Consequences.** Adding a language means adding a column to the catalog. The Montenegrin strings should be proofread by a native speaker (see Questions). The bot never confirms a request; staff confirm.

---

## D-009 - Hybrid intent classification, with rules authoritative for safety

**Decision.** Multilingual lexicon rules (folded text, inflection wildcards, late-time parsing) always run. EMERGENCY and HUMAN_REQUEST detected by rules are final. Otherwise, when an LLM is configured, it classifies with a strict JSON schema, and invalid or low-confidence output falls back to the rules. As a third signal, a message classified UNKNOWN that retrieves grounded knowledge is answered as information.

**Reason.** Safety must not depend on a network call or model mood. The LLM handles paraphrase and nuance, and the rules handle the rest.

**Alternatives.** LLM only (brittle when the provider is down); rules only (brittle on paraphrase).

**Consequences.** Without an LLM, the rules are the classifier. They are tuned on the test table in `tests/test_intents.py`, which should grow with ChatGPT's evaluation cases. The intent set is the one in the brief. Billing and booking-change are *flags* on an intent, not separate intents.

---

## D-010 - Actions vs knowledge: service requests always go to staff as `HotelRequest`s

**Decision.** SERVICE_REQUEST and BOOKING_REQUEST always create a `HotelRequest` through the `create_hotel_request` tool. If the knowledge pack has a relevant policy item (`metadata.requires_staff_approval` lists the request type), the bot quotes it first. Then it says the request was sent and **is not confirmed yet**. A question about an actionable topic without an explicit ask ("Is late check-in possible?") is answered from knowledge, and the bot offers to send a request. A "yes" creates the request.

**Reason.** This follows the brief's "Can we check in after 11pm?" example and never hallucinates a confirmation.

**Alternatives.** Let the bot approve requests that policy fully covers. Rejected for now: no PMS or availability data exists, so the bot cannot know.

**Consequences.** Staff work a request queue. A future slice can auto-approve when policy and a PMS integration allow it.

---

## D-011 - Handoff semantics: a human owns the conversation until they hand it back

**Decision.** A handoff sets the conversation to `handed_off`. From then on the bot stores guest messages but does not reply. The exception is an EMERGENCY, which always gets the emergency reply and escalates the open handoff's urgency. Staff `accept` and then `resolve` the handoff, which hands the conversation back to the bot or closes it. One open handoff per conversation: new triggers update it instead of creating duplicates.

Handoff triggers: emergency (critical), explicit human request, complaint (high), billing dispute (high), booking change/cancellation, and **2 consecutive turns the bot could not help with** (`HOTELBOT_MAX_CONSECUTIVE_FAILURES`). A plain new booking or availability question creates a BOOKING request without a handoff.

**Reason.** It avoids the bot and a human talking over each other, which is the classic handoff failure.

**Alternatives.** Bot keeps answering during handoff; time-based automatic return to the bot.

**Consequences.** If staff never resolve a handoff, the bot stays silent for that guest. The MVP needs staff discipline, and a later slice should add an SLA timeout and notification.

---

## D-012 - Deterministic handoff summary

**Decision.** The handoff package (guest/session id, channel, language, reason, urgency, intent, guest request, summary, guest-stated facts, requests, last N messages) has a template-built summary, not an LLM summary.

**Reason.** A summary must never misstate what the guest said. The full recent transcript is attached anyway.

**Consequences.** Summaries are mechanical. An optional LLM summary can be added *alongside* the deterministic one.

---

## D-013 - Session memory stores guest statements as unconfirmed facts

**Decision.** Arrival date, departure date, guest count and room number are extracted with deterministic parsers (English and Montenegrin date and number formats). They are stored on `Conversation.memory` as `{"value", "source": "guest_stated", "confirmed": false}`. Dialogue state (pending yes/no offers) lives under the reserved `_state` key.

**Reason.** The brief says "do not treat unconfirmed inferred details as facts".

**Consequences.** Only staff or a future PMS integration should set `confirmed: true`. Memory is per conversation. Cross-stay guest profiles are future work.

---

## D-014 - Language: `en` + `cnr`; Serbian/Croatian/Bosnian answered in Montenegrin

**Decision.** A dependency-free detector uses Cyrillic/diacritic signals and function-word votes. Montenegrin is ISO 639-3 `cnr`. Russian Cyrillic is recognised (so it is not read as Montenegrin), but it is not a supported reply language. A weak or absent signal ("ok", "👍") keeps the conversation's language. The bot replies in Latin script.

**Consequences.** Unsupported languages are answered in the conversation language or in English. Adding a language means adding a word list, a catalog column and pack translations. A guest writing in Cyrillic gets Latin replies (see Questions).

---

## D-015 - WhatsApp: ack fast, process in background, idempotent on `wamid`

**Decision.** `POST /webhooks/whatsapp` checks `X-Hub-Signature-256` (HMAC-SHA256 with the app secret, required in `prod`), validates the payload with Pydantic, returns 200 immediately and processes messages in a FastAPI background task. Messages are deduplicated on the WhatsApp message id (unique `messages.external_id`). Status callbacks are ignored. Non-text messages get a polite "text only" reply. `HOTELBOT_WHATSAPP_TRANSPORT=mock` swaps in an in-memory outbox (`GET /api/dev/outbox`).

**Reason.** Meta retries slow or non-2xx webhooks, so without dedupe the guest gets duplicate replies.

**Alternatives.** A durable queue (Redis/RQ, Postgres `SKIP LOCKED`).

**Consequences.** A crash between the ack and processing loses that message. That is acceptable for a PoC. A Postgres-backed job table is the upgrade path (no Redis needed). Outbound sends are not retried yet.

---

## D-016 - Staff API secured by a shared token; no UI yet

**Decision.** `/api/staff/*` requires `X-Staff-Token` (constant-time compare). Without a configured token it is open only when `HOTELBOT_ENV=dev`, and returns 503 otherwise.

**Consequences.** There are no per-staff identities or audit trail beyond the optional `staff_name`. Real auth (per-user accounts or SSO) comes with the staff UI.
