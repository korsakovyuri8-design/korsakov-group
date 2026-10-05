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

---

# Iteration 2: Stay Engine and evaluation harness

## D-017 - Core v1 frozen as a baseline; compatibility is tested, not assumed

**Decision.** Commit `2a73fc1` is tagged `hotelbot-core-v1` (local tag; this session's git proxy cannot push tags). All 159 Core v1 tests must keep passing. Exactly one v1 assertion changed, because of an explicit product decision: Russian is now a supported language (`tests/test_language.py`, marked in place). The `tests/conftest.py` PostgreSQL reset helper now reflects and drops *all* tables, so leftover v1 tables no longer block it. No assertion changed there.

**Reason.** Generalising the domain must not silently change v1 behaviour.

**Consequences.** Several v1 names survive as aliases: `Hotel`, `HotelKnowledgeDocument`, `HotelRequest`, `HotelRepository`, `report.hotel_id`, `create_hotel_request`, `/api/staff/requests`, `ActionTaken.kind == "hotel_request"`, the `HOTEL_INFORMATION` intent and `HOTELBOT_HOTEL_SLUG`. They can be removed in a versioned API change once no client needs them.

---

## D-018 - Property replaces Hotel in the core

**Decision.** `Property` (table `properties`) has `property_type` ∈ {hotel, hostel, resort, vacation_rental, apartment, glamping, other}, plus `timezone`, `default_language`, `active`, `capabilities` and metadata. Knowledge becomes `KnowledgeDocument` (`property_id`). Pack v2 uses `property_slug` and `property_name`. The v1 names `hotel_slug` and `hotel_name` are still accepted. Nothing location-specific is hard-coded in the core: the emergency number (`112`) moved into the pack (`pack.emergency_number`). If a pack does not set it, the emergency reply says "the local emergency number".

**Consequences.** Hotel Aleksandar will be one pack with `property_type: hotel`. A second synthetic pack (`data/properties/demo_apartment.yaml`, a vacation rental) shows that the same core serves a property with different capabilities.

---

## D-019 - Stay is the context; stay facts, authoritative fields and guest preferences are kept apart

**Decision.** `Stay` connects one Guest and one Property for one visit. Every conversation belongs to a stay. The agent keeps three buckets:

| Bucket | Where | Written by |
|---|---|---|
| Authoritative stay data (booking ref, dates, party size, status) | `Stay` columns | staff (`PATCH /api/staff/stays/{id}`), future PMS |
| Guest-stated facts (unconfirmed) | `Stay.facts` | agent, always `confirmed: false` |
| Global preferences (e.g. language) | `Guest.preferences` | agent (`source: observed`) |

A message is attached to the guest's open stay at that property: status inquiry, booked or in_house, and not more than 2 days past a verified departure. Otherwise a new stay starts. Facts therefore cannot leak across properties or visits. `Conversation.memory` holds dialogue state only.

**Alternatives.** Facts on the conversation (v1); one global guest profile. The first leaks between visits once conversations are reused. The second mixes verified and unverified data.

**Consequences.** Without a PMS, stays are `inquiry` until staff verify them. Matching an inbound guest to a *booking* (by booking reference or phone) is a future slice.

---

## D-020 - Generic Action with an enforced lifecycle and audit trail

**Decision.** `Action` (table `actions`) replaces `HotelRequest`. It has `action_type` (a capability key), `status` (one of the eight lifecycle states), `executor`, `params`, `result`, `external_ref` and `error`. Every transition goes through `ActionService.transition()`, which validates it against `ALLOWED_TRANSITIONS` and writes an `ActionEvent` (from, to, actor, detail). Terminal states have no exits. Staff may skip intermediate steps (submitted → completed), because completion implies acceptance. The event log still shows exactly what was recorded. `PROPOSED` exists but conversational offers are not persisted as actions; it is reserved for flows that need explicit guest confirmation (price quotes in the transport slice). `/api/staff/requests` stays as a v1 view: pending = submitted; in_progress = accepted + in_progress; resolved = completed; rejected = rejected + failed + cancelled.

**Consequences.** The intent layer still speaks in request *topics* (`RequestType`). `app/actions/catalog.py` maps topics to action types.

---

## D-021 - Capability registry per property, declared as data

**Decision.** Each pack declares `capabilities.actions` (which action types exist and which executor handles each), `integrations` (`human_staff`, later `pms`, `payment`) and informational `external_services`. Knowledge capabilities are derived from the pack's topics. The orchestrator never assumes an action exists; it asks `registry.can(action_type)`. If the action is unavailable, the bot quotes any relevant property knowledge ("fresh towels are in the wardrobe"), says what it cannot arrange, and offers to ask staff. Nothing is faked. Validation rejects unknown action types, unknown executors, webhook entries without a URL, and staff executors without `human_staff`. Packs without a `capabilities` section get the v1 behaviour: every action, executed by staff.

**Alternatives.** Capabilities in code per property type. Rejected: two hotels differ as much as a hotel and an apartment do.

**Consequences.** Connecting a property to a new integration is a pack change, with no change to the agent core.

---

## D-022 - Response Authority invariant

**Decision.** *LLMs may understand, phrase, summarise and classify; they may not decide whether a real-world event happened.* It is enforced in three places:

1. **Templates per state.** `authority.status_message(action)` is the only producer of sentences about an action. There is one fixed template per `ActionStatus` per locale, and each may claim exactly its own state. `tests/test_authority.py` checks every status in every locale.
2. **Guard on model prose.** `unbacked_claims(text, statuses)` detects acceptance-level claims (confirmed / approved / booked / on its way / "you can stay until") and completion-level claims (done / fixed / cleaned), with negation handling, in en, cnr and ru. A grounded LLM answer that makes a claim no stored action backs is discarded. The bot falls back to the property-authored text.
3. **Global evaluation check.** The harness runs (2) on every bot message in every scenario. Property-authored knowledge that the bot quotes verbatim is excluded, because it is the property's own published policy.

The detector errs towards flagging. A false positive only costs nicer LLM phrasing; a false negative would break the invariant.

**Consequences.** When real integrations arrive (payments, PMS, transport), statements about them stay tied to recorded state by construction.

---

## D-023 - Webhook executor: failure is FAILED, then honest fallback to staff

**Decision.** `WebhookExecutor` POSTs the action to a URL from the pack. It signs the body with `X-HotelBot-Signature` when `secret_env` names an environment variable; secrets never live in packs. The partner responds `accepted`, `received` or `rejected`. Timeouts, transport errors, HTTP ≥ 300 and malformed bodies all become `FAILED` with an error; none is ever an assumed success. On failure, if the property has `human_staff`, the same action is re-submitted to the staff queue. The guest hears "I couldn't submit it automatically, so I've sent it to the staff; it is not confirmed yet."

**Consequences.** There are no retries and no inbound status callbacks yet. Both belong to the transport slice (callback endpoint + signed status updates through `ActionService.transition`).

---

## D-024 - Alembic, with automatic upgrade on startup

**Decision.** Two revisions exist. `0001_core_v1` is the v1 schema verbatim. `0002_stay_engine` is forward-only and copies data:

- `hotels` → `properties`
- knowledge documents are re-keyed to `property_id`
- one stay is created per existing conversation, and guest-stated facts move from `conversation.memory` to `stay.facts`
- `hotel_requests` → `actions`, with synthesized audit events

`create_schema()` handles three cases:

- **Empty database:** `create_all` and stamp head.
- **Existing database:** `alembic upgrade head`.
- **v1 database:** v1 never had an `alembic_version` table, so it is stamped `0001` first and then upgraded.

This is controlled by `HOTELBOT_AUTO_MIGRATE`.

**Verification.** Tests check that the schema produced by migrations equals the models. They also seed v1 data and assert it after upgrade. Both run on SQLite and PostgreSQL. A real v1 PostgreSQL database left over from the Iteration-1 demo was migrated by simply starting the new server.

**Consequences.** Back up before upgrading production data (`pg_dump`); the downgrade raises. The migrations caught a real bug during development: untyped columns read datetimes and JSON back as strings on SQLite.

---

## D-025 - Locales: Cyrillic out for Cyrillic in; Russian for routing and operations

**Decision.** Reply locales are `en`, `cnr`, `cnr-Cyrl` and `ru`. Montenegrin or Serbian written in Cyrillic gets Cyrillic replies. Templates and Latin knowledge are transliterated deterministically. Placeholders, numbers, acronyms and loanwords such as Wi-Fi and check-in are preserved. Detection distinguishes Russian from Serbian/Montenegrin Cyrillic using script-specific letters, then a list of non-shared function words. Russian has full template coverage and Russian intent lexicons. Knowledge in Russian is not required: a Russian guest gets the English text prefixed with "this information is only available to me in English". With an LLM enabled, the model phrases it in Russian. A weak signal keeps the conversation locale. A new stay reuses the guest's global language preference. German is deferred.

---

## D-026 - Failure threshold is a policy object

**Decision.** `FailurePolicy(default, per_intent)` is resolved in this order:

1. the property pack's `policy` (`max_consecutive_failures`, `failure_thresholds`)
2. the deployment settings (`HOTELBOT_MAX_CONSECUTIVE_FAILURES`, `HOTELBOT_FAILURE_THRESHOLDS`)
3. the default of 2

The threshold applied is the one for the intent of the turn that failed. Example: the demo apartment escalates an unanswerable local recommendation after 1 failure, while the hotel keeps 2 for FAQs.

---

## D-027 - New intent: REQUEST_STATUS

**Decision.** "Is my late checkout confirmed?" is a distinct intent, answered only from stored action state via `status_message`. If no action exists, the bot says so.

**Reason.** Without this intent, status questions fell into FAQ retrieval. That invites exactly the confirmation hallucination the authority invariant forbids. This is a change to the intent set the brief proposed; flagged for ChatGPT's conversation-policy track.

---

## D-028 - Knowledge answers: clauses, follow-ups, conflicts, partial answers

**Decision.** Four deterministic dialogue rules apply to knowledge answers:

- **Multi-question messages** are split into clauses using per-language conjunctions. English "I" is not the Montenegrin conjunction "i". Each clause is grounded separately. Answered parts are given, and the unanswered rest gets "I can't confirm the rest of your question".
- **Short anaphoric follow-ups** ("Is it free?", "A parking?") are retrieved together with the previous question.
- **Conflicts.** Items sharing a `topic` but disagreeing get "I have conflicting information", with no guess. Ingest logs `knowledge_conflict_risk` for shared topics.
- **Statement-only messages** ("we are 2 adults arriving 20 Dec") are acknowledged as noted. The reply explicitly says nothing was changed or confirmed.

---

## D-029 - Product evaluation harness: gates and benchmark

**Decision.** `evals/` is separate from unit tests. Scenarios are YAML: messages, seed-knowledge edits, capabilities, scripted LLM, mocked integration, staff transitions, stay updates. Expectations cover intent, language, grounding, sources, forbidden claims, actions, handoffs, and stay memory. Every scenario runs through a fresh real container via `Orchestrator.handle()`, and every bot message gets the global authority check. Scenarios marked `gate: true` encode product invariants (authority, safety, memory isolation, capability honesty). They are pytest tests and they set the CLI exit code. The rest is a benchmark, reported as a pass rate (`python -m evals --markdown docs/EVAL_REPORT.md`).

Scenario authoring rule: expectations describe *desired product behaviour*. A failing benchmark scenario is diagnosed and reported, not tuned away. Two scenario mis-specifications found during the first run were fixed and recorded. They were forbidden phrases that also matched correct text: "is booked" inside "Nothing is booked yet", and the pack's own policy wording.

---

## D-030 - Retrieval: no embeddings yet

**Decision.** The lexical coverage retriever stays. Evidence is in `docs/RETRIEVAL_ANALYSIS.md`. After fixing one real lexical bug (stopword-stem collision), the remaining grounding failures (4 of 12) are paraphrases. Precision scenarios (unsupported facts, false premise, removed knowledge, conflicts) pass 100%. That is the safety-relevant side that a semantic retriever tends to weaken.

**Consequences.** Next: grow the paraphrase set from real guest messages, then compare against the same harness: (a) curated pack keywords, (b) LLM query rewriting into pack vocabulary, (c) hybrid lexical + embeddings, with lexical kept as the measured baseline and the precision scenarios as a gate.
