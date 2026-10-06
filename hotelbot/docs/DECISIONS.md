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

---

# Iteration 3: external service transactions and the local travel layer

## D-031 - Quotes are first-class and are not bookings

**Decision.** Every provider-backed service goes REQUEST → QUOTE → CONSENT → TRANSACTION. A `Quote` belongs to one conversation and has a 4-letter code, a price, conditions, `valid_until`, and a status: OFFERED, ACCEPTED_BY_GUEST, DECLINED_BY_GUEST, EXPIRED or SUPERSEDED. Only an accepted quote creates an `Action` (executor `provider`) and an `ExternalTransaction`, and they reuse the Action state machine (PROPOSED → SUBMITTED → ACCEPTED → IN_PROGRESS → COMPLETED | REJECTED | FAILED | CANCELLED).

**Reason.** A price offer must never read as a booking. Reusing the Action lifecycle keeps one authority model, one staff view and one set of status templates.

**Alternatives considered.** A separate booking state machine was rejected: it duplicates authority templates and staff tooling. Booking straight from a request ("book a taxi" → SUBMITTED) was rejected: the guest has not seen or accepted a price.

**Consequences.** "Is my transfer booked?" while only a quote exists gets "there is a price offer (CODE), nothing is booked". The ExternalTransaction is unique per action and per quote, which makes double-acceptance impossible at the database level.

---

## D-032 - Consent is explicit, scoped and strict

**Decision.** Consent counts only if:

- the whole message is an explicit affirmative ("yes", "book it", "da, rezervišite", "да, бронируйте"), optionally with the code;
- it refers to an offer the bot presented in its previous message, or one the guest names by code or service word ("book the transfer and the skis");
- the offer has not expired.

The following are never consent:

- "ok", "ok maybe", "sounds good?" and emoji;
- anything that changes details (that produces a new offer);
- a bare "yes" when several offers are open (the bot asks which).

"Book all" accepts every open offer. A targeted decline ("cancel only the guide") declines that offer only.

**Reason.** Consent creates a real obligation with a third party. False positives are far more expensive than one extra question.

**Alternatives considered.** An LLM consent classifier was rejected as non-deterministic and impossible to audit. Accepting "ok" was rejected because it is too often filler.

**Consequences.** Consent is recorded on the quote (message id, text, timestamp) and on the action audit trail.

---

## D-033 - Provider adapters: one interface, mock and webhook implementations

**Decision.** `ProviderAdapter` has four methods: `request_quote`, `submit`, `cancel` and `get_status`.

- `MockExternalProvider` is deterministic, with configurable pricing and failure behaviours (timeout, unavailable, invalid, auth, flaky:N, rejected, received). It deduplicates by idempotency key.
- `WebhookExternalProvider` implements a documented HTTP/JSON contract (`docs/PROVIDER_INTEGRATION.md`). Requests are HMAC-signed with a timestamp and carry an `Idempotency-Key` header.

Errors are classified:

- Retryable: `ProviderTimeout`, `ProviderUnavailable` (5xx, 429, network, malformed body).
- Permanent: `ProviderInvalidRequest`, `ProviderAuthError`.

Secrets live only in environment variables named by `*_env` keys. The pack schema rejects secret-looking keys.

**Reason.** New verticals and partners should be configuration plus an adapter, never orchestrator changes.

**Consequences.** A real partner either speaks the webhook contract or gets a small adapter class registered in `ProviderRegistry`.

---

## D-034 - Reliability: transactional outbox on PostgreSQL, no Redis

**Decision.** Side effects run as jobs in a `jobs` table:

- provider submit and cancel;
- guest notifications;
- WhatsApp inbound processing and replies.

Jobs are enqueued in the same database transaction as the state change (outbox). Workers claim with `FOR UPDATE SKIP LOCKED` under a 5-minute lease. Retries use bounded exponential backoff (5 s doubling, capped at 300 s, with a per-kind `max_attempts`). After that a job is DEAD and runs its `on_dead` hook: the transaction becomes FAILED, the guest gets an honest message, and staff get a handoff.

Idempotency keys: `txn-{quote}` (provider), `submit:{txn}`, `cancel:{txn}`, `notify:{action}:{status}`, `inbound:{wamid}` and `reply:{wamid}`.

**Reason.** One datastore, exactly-once *effects* through idempotency (not exactly-once delivery), and crash safety without extra infrastructure.

**Alternatives considered.** Redis/RQ or Celery add an extra moving part and a second source of truth. In-request provider calls block the webhook and lose work on a crash.

**Consequences.** On SQLite a single worker is assumed (tests and demo). PostgreSQL supports several `python -m app.worker` processes; a concurrency test proves no job runs twice. Handler side effects roll back with the failed attempt.

---

## D-035 - Provider callbacks: signed, fresh, idempotent, legal

**Decision.** Endpoint: `POST /api/providers/{property}/{provider}/callbacks`.

- The body must be signed with HMAC-SHA256 over `"{timestamp}.{body}"`, using the provider's `callback_secret_env`.
- Timestamps outside a 300 s window are rejected.
- `(provider, event_id)` is unique and is claimed *before* acting, so concurrent duplicates cannot both apply.
- An unknown reference gets 404. An illegal transition (e.g. COMPLETED → ACCEPTED) gets 409 and changes nothing.

Every event is stored as a `ProviderEvent` with its outcome.

**Reason.** Callbacks are the only way provider state enters the system, so they are a security boundary and an audit trail.

**Consequences.** Without a configured secret, callbacks for that provider are refused (503), never accepted unsigned.

---

## D-036 - Language policy for fixed messages

**Decision.** Operational messages (quotes, statuses, failures) are manual templates in en / cnr / ru. `cnr-Cyrl` is derived by transliteration with placeholder protection. Packs do not need Russian content. Provider-supplied text (conditions, offering titles) is shown verbatim, as data.

**Consequences.** The authority guard can check every template. Guests may see a provider's conditions in the provider's language.

---

## D-037 - Local travel layer: generic entities, not one engine per vertical

**Decision.** The layer has five generic tables:

- `Place` (venues, shops, services, attractions);
- `Event` (dated happenings);
- `Offering` (something bookable at or by a provider);
- `AvailabilitySlot` (inventory);
- `ItineraryItem` (the trip plan).

Variation lives in `category` (19 top-level values), a `subcategory` from a code taxonomy (`app/places/taxonomy.py`, localized labels and keywords), and structured JSON `attributes` (cuisine, diet, accessibility, pets, noise, age limits, cover charge...). There are no per-subcategory columns. Every row carries provenance: `source`, `last_verified_at`, `confidence`, `provider_owned`, `is_synthetic`.

Region data is a YAML **region pack** (`data/regions/*.yaml`) with places, events, offerings and region-scoped providers. `ExternalProvider` gains a `region` and an optional `property_id`.

**Reason.** Restaurants, ski rental, guides, pharmacies and events differ in data, not in mechanics. A new vertical should be data + a provider adapter + a capability + policies.

**Alternatives considered.** Separate restaurant / rental / event engines would mean duplicated hours, availability and booking logic. A free-form "points of interest" text corpus for RAG was rejected because it cannot answer "open now" or "fits 4 people" reliably.

**Consequences.** Times in packs are local and stored as UTC. Hours are structured (weekly, kitchen, seasonal, special days, temporary closures, last entry), so OPEN / OPEN_LATER / CLOSED is computed, never guessed. Freshness (fresh / stale / unverified) is shown as a caveat.

---

## D-038 - Discovery: retrieve → filter → rank → explain, from structured data only

**Decision.** Discovery requests are parsed deterministically (en / cnr / ru) into a `DiscoveryQuery`:

- **Hard constraints:** categories, exclusions, diet, accessibility, pets, open-at, kitchen-serving-at, open-after-midnight, distance.
- **Soft preferences:** lively / quiet, local, tags.

Hard constraints filter. Ranking only orders what passed, and a soft preference never returns its opposite ("lively" never yields a place recorded as quiet). Every reason and caveat in a result is derived from stored fields: hours, distance (haversine from the property), attributes, freshness. Past events are never shown.

The property's own knowledge goes first when its pack answers the same kind of need ("Where is the parking?"), unless the guest uses an explicit outside cue ("nearby", "in town", "recommend").

**Reason.** "The LLM never defines reality" applies to places too: names, hours and distances from a model's memory are exactly the hallucinations a travel product cannot afford.

**Consequences.** If nothing matches, the bot says so and offers staff, without guessing. Health results add the property's emergency number only (never an invented one); emergencies themselves are handled by the safety path before discovery runs.

---

## D-039 - DISCOVERY, RESERVATION, TRANSACTION and CONFIRMATION stay separate

**Decision.** The four stages are separate steps, and the guest can see where each item is:

1. Discovery shows options ("save 1", "book 1"). Saving creates a SAVED plan item, never a reservation.
2. "Book N" on a venue with a reservation offering starts a reservation, which is a quote.
3. Consent creates the transaction.
4. Only a provider response or callback confirms it.

A place without a reservation channel is "can't be reserved through me (walk-ins welcome)", never "booked".

**Consequences.** `ItineraryItem` statuses linked to a quote or action are read from the source row, so the plan can never disagree with the transaction.

---

## D-040 - Availability is recorded inventory or the provider's word

**Decision.** `AvailabilitySlot` rows hold capacity. Units are people by default; `unit: group` counts whole bookings, up to the offering's `max_party`. Accepting a quote holds the units, and REJECTED / CANCELLED / FAILED release them. A quote is matched to the first offering that fits: capacity, language and party size. Otherwise `NoAvailability(reason, alternatives)` returns the nearest real slots. Possible reasons: `no_capacity`, `not_offered_at_that_time`, `language_unavailable`, `party_too_large`.

If an offering has no modelled inventory, the provider decides at quote or submit time.

**Consequences.** The flagship example works from data alone. "Skis for 4" skips a shop holding 3 sets. "Russian guide Sunday" picks the guide who works Sundays. A group of 8 gets real alternatives instead of a fake slot.

---

## D-041 - Multi-part trip requests become independent plan items

**Decision.** A message with several requests is split into sentences. Each sentence becomes an independent item:

- a provider service (quote, or the one missing detail to ask for);
- or a discovery shortlist.

Trip context is extracted once and applied only where the item lacks it:

- arrival day and time;
- party size;
- for transfers: pickup time = arrival, destination = property (both shown as part of the offer).

The ETA is arrival plus the transport provider's `estimated_duration_minutes`, shown as an estimate. "Still serving when we arrive" filters kitchens at the ETA. All offered codes are awaiting consent together. A missing detail ("What time on 17.01?") is kept as a draft, and its answer goes to that draft even while other offers are open.

**Reason.** Guests write like this, and each item has its own provider, price, availability and status. One failing item must not block the others.

**Alternatives considered.** A single "trip booking" transaction was rejected: there would be no partial success and no selective consent.

**Consequences.** Segmentation is sentence-based. Several requests inside one sentence ("a taxi and a table") are not yet split (see the known limitations).

---

## D-042 - Traveller preferences and contextual recommendations: architecture only

**Decision.** Preferences come only from what the guest says explicitly (Stay facts and Guest.preferences, as before). Nothing is inferred from behaviour. Contextual recommendations (weather, time of day, plan gaps) and a direct tourist mode (no property) are designed to plug in as additional `DiscoveryQuery` producers. The engine already takes `near`, `at` and region without a property, but they are not built in this iteration.

---

## D-043 - Timestamps: local in packs, UTC in the database

**Decision.** Region pack times (events, slots, verification dates) and plan item times are converted from the region timezone to UTC at write time.

**Reason.** SQLite drops offsets. Storing local wall time silently shifted every event by the zone offset; the evals caught it ("DJ night 01:00" instead of 23:00). File-based SQLite also needed explicit BEGIN handling for correct SAVEPOINT rollback; the in-memory test engine shares one connection and documents that limitation.

---

# Iteration 3, second addendum: service marketplace (transport, rentals, guides)

## D-044 - One marketplace model, three first-class families

**Decision.** Transport, rentals and guides/tours share one model:
`ExternalProvider` (profile, policies, commercial metadata) → `Offering` (pricing, policies, attributes, inventory) → `Quote` → `ExternalTransaction` on the Action state machine.

Vertical behaviour lives in data:

- **Transport** offerings declare `max_passengers`, `luggage_capacity`, `child_seats`, `vehicle_types` and a timed window.
- **Rentals** are ONE `rental` service with a `category` field (skis, snowboard, bicycle, e-bike, car, scooter, hiking or camping equipment). An offering lists its `categories`, sizing `variants` and the details it `requires` (e.g. heights).
- **Guides** declare `languages`, `specialties`, `format` (private/group), `max_party`, schedule, `weather_dependent`, meeting point, difficulty, inclusions and exclusions.

There is no engine per vertical.

**Reason.** "Change the skis to snowboards" and "move my taxi to 7am" must use the same machinery. A new rental category is one line of taxonomy plus offerings, not a schema change.

**Alternatives considered.**

- `ski_rental` / `car_rental` / `bike_rental` services (Iteration 3's first cut): rejected because they multiply services and flows.
- A per-vertical booking table: rejected because it duplicates consent, status and authority.

**Consequences.** `ski_rental` and `car_rental` services were replaced by `rental` (packs updated). Provider types: transport, rental, guide, restaurant, nightlife...

---

## D-045 - Provider discovery returns structured candidates; relationships shape, never override

**Decision.** `app/marketplace/discovery.py` evaluates every active offering of every provider in scope (the property's own partners plus the region's shared providers). Each check rejects or keeps it, in this order:

1. static fit (category, language, specialty, format, vehicle size, child seats, luggage, group size, minimum duration);
2. offering-specific missing details;
3. a concrete time window;
4. live inventory;
5. a price estimate from the offering's pricing.

Property relationships (`provider_relationships` in the property pack → `property_providers`):

- BLOCKED removes a provider.
- EXCLUSIVE restricts its services to that provider.
- DEFAULT, PREFERRED and a capability's declared provider rank first among suitable candidates.

Ranking never re-admits a candidate that failed a constraint. Guides are presented as one option per format (private vs group) when the guest has not chosen one. Otherwise the best candidate is quoted.

**Reason.** "Do not allow the LLM to invent providers." The same function serves a property or, with `property_id=None`, a traveller without one.

**Consequences.** Providers are not owned by properties (`property_id` nullable since 0004). The demo transfer partner moved from the hotel pack to the region pack and is the hotel's DEFAULT transport relationship.

---

## D-046 - Inventory = capacity minus live holds (InventoryHold)

**Decision.** `AvailabilitySlot` holds capacity only. Bookings are `InventoryHold`s (offering, variant, window, quantity, status HELD/CONFIRMED/RELEASED, `expires_at`, quote, transaction):

- A hold is created when a QUOTE is made and expires with it.
- Consent confirms it.
- Decline, supersede, expiry, rejection, cancellation and failure release it.

Availability counts CONFIRMED holds plus HELD holds that have not expired, so an expired quote frees stock with no sweeper. Hold creation takes a row lock on the offering (`SELECT ... FOR UPDATE` on PostgreSQL), and a concurrency test proves the last item cannot be held twice. A change of a booking may reuse that booking's own stock (`ignore_transaction`).

Windows:

- "slot": the slot that contains the start. Multi-day rentals need a slot on every day. Fixed-schedule offerings resolve a day-only request to their scheduled start and quote that start, never the guest's guess.
- "duration": start plus `duration_minutes` (transfers).

Variants: sizes from heights via offering bands (ski or board length, bike S/M/L).

**Alternatives considered.** Decrementing `remaining` on the slot (Iteration 3 first cut) was rejected: it needs a sweeper for expiry, loses bookings when slots are re-ingested, and cannot represent quote-time holds.

---

## D-047 - Material terms are part of the offer and of the consent

**Decision.** Provider policies are merged with offering policies and attributes into `quote.terms`:

- deposit, damage deposit, ID, licence, minimum age, minimum duration;
- pickup hours, return rules, late return fee;
- meeting point, duration, difficulty, equipment, included and excluded items;
- weather dependency, free-cancellation window, cancellation fee.

The terms are rendered ("Important terms: ...") with the price, and the consent record stores the exact terms the guest agreed to. No deposit or payment is processed.

---

## D-048 - PENDING_CONDITION for weather-dependent services

**Decision.** New Action status between SUBMITTED and ACCEPTED: a provider may accept *subject to a condition*. Signals are the mock/webhook outcome `accepted_conditional` and the callback `conditional`. The guest is told "provisionally accepted, subject to the weather - not confirmed yet". The template backs no confirmation claim (authority tests). The callback `condition_met` moves it to ACCEPTED, and `condition_failed` to CANCELLED ("called off because of the weather"). Plan view: "provisional - subject to weather, not confirmed".

---

## D-049 - Changes are replacement offers; cancellation policy is checked before promising

**Decision.**

- **"Move my taxi to 7am" / "Change the skis to snowboards".** The confirmed booking's details plus the change form a NEW offer that replaces it (`quote.replaces_transaction_id`). Nothing changes before consent.
  - If the provider supports modification (`supports_modification: true`, adapter `modify()`), the booking is modified in place (same reference), and the old record ends as CANCELLED ("replaced").
  - Otherwise the new booking is submitted, and the old one is cancelled only once the new one is ACCEPTED. A failed change never leaves the guest with nothing.
- **A change the bot cannot pin down** still goes to staff.
- **Cancellation.** Before promising anything, the bot reads `free_cancellation_hours` and `cancellation_fee` from the quote's terms.
  - Inside the window, it says the cancellation is free and requests it.
  - After the deadline, it states the fee and cancels only after "yes, cancel".
  - Without a policy, the provider decides (as before).

---

## D-050 - Multi-service conversations: split per service, decide per sentence

**Decision.**

- One sentence asking for several services is split at the delimiter before each service's keyword, with positions mapped through `fold()` so Cyrillic works.
- Trip context fills only what an item lacks: party, arrival day and time, origin ("from Podgorica"), destination = property. Everything filled is shown in the offer.
- A vague "Friday evening" is never a pickup time: the transfer asks for the exact time.
- Several missing details are asked one at a time (a queue of drafts). An answer always goes to the draft asked about, even while offers are open, and asking does not withdraw the open offers.
- Replies to open offers are read sentence by sentence:
  - "Book the transfer and the guide. I'll decide about the skis later." books two and keeps the skis offer open, said explicitly.
  - Any sentence that is neither a decision nor a deferral cancels the whole interpretation (nothing booked).
  - "book all" names every open offer.
- A new request that does not name an open offer is a new request, not an edit of that offer.

---

## D-051 - Commercial metadata without commercial influence

**Decision.** Providers and offerings carry `commission_type` / `commission_value`; offerings also carry `partner_price` / `guest_price`. Each quote stores a `commercial` snapshot: guest price, commission, partner price. The price shown and consented to is the quote amount, and commission never changes it or the ranking of unsuitable candidates. Enough for commission-per-booking, markup or revenue-share accounting later. No accounting is built.

---

# Architecture correction: two worlds, four truths

## D-052 - LOCAL WORLD and TRANSACTION WORLD are separate layers

**Decision.** The product has two orthogonal layers.

**LOCAL WORLD (discovery)** — FIND / RECOMMEND / SAVE:
- `Place` (restaurant, bar, museum, pharmacy, ATM, supermarket, viewpoint, coworking, parking, hospital, venue) and `Event`;
- modules `app/discovery`, `app/places`.

**TRANSACTION WORLD (execution)** — QUOTE / BOOK / ORDER / CHANGE / CANCEL:
- `ExternalProvider`, `Offering`, `Quote`, `ExternalTransaction`, inventory;
- modules `app/transactions`, `app/marketplace`.

A real business may live in both. A restaurant is a Place, and *if* it takes reservations, it also has a reservation Offering (`offerings.place_id`). An event is an Event with an optional ticket Offering (`offerings.event_id`, migration 0006). A pharmacy, an ATM or a viewpoint is only a Place — never a provider "for consistency". A taxi is a provider and offering with no Place.

The only code that connects the worlds is `app/marketplace/bridge.py` (`options_for(place | event)`), used by the orchestration layer (`app/trip`, `app/agent`). `tests/test_architecture.py` fails the build if the discovery world imports transaction code, or the reverse.

That test immediately found two real couplings, both fixed:
- discovery NLU imported time parsing from `app.transactions.slots` (moved to the neutral `app/nlp/temporal.py`);
- the transaction dialogue wrote stay-plan items itself (now `PlanHooks`, implemented by `app/trip/itinerary.StayPlanHooks`).

Inventory moved from `app/places/availability.py` to `app/marketplace/inventory.py`: it is the inventory of offerings, i.e. the transaction world.

Capabilities are split the same way:
- `capabilities.discovery`: enabled, categories, events;
- `capabilities.actions` / `capabilities.services` (transactions).

`describe()` reports `discovery_capabilities` and `transaction_capabilities`. A guest message is also classified into operations (`app/agent/operations.py`): FIND / RECOMMEND / SAVE vs QUOTE / BOOK / ORDER / CHANGE / CANCEL. The result is logged on every turn as the layer it touches.

**Alternatives considered.**
- Everything as ExternalProvider/Offering: a "service catalogue" that slowly becomes a dump of everything a tourist meets. Hours, freshness and geo would end up on providers; ATMs would get fake transaction semantics.
- Everything as Place with booking flags: transactions without places (taxi) don't fit, and there is no quote/consent/inventory model.

**Consequences.** New verticals choose their side explicitly. A large recommendation engine is deliberately not built yet; the discovery interfaces stay minimal.

---

## D-053 - Four kinds of truth; the LLM is the interface to them, never the source

| Truth | Example | Source |
|---|---|---|
| Knowledge | "What time is breakfast?" | property pack / place data |
| Availability | "Is a table free at 21:00?" | provider or modelled inventory (slots minus holds) |
| Transaction | "Has the taxi been booked?" | stored Action / ExternalTransaction state, provider callbacks |
| World (time-sensitive) | "Is this restaurant open now?" | structured place/event data + clock + freshness |

Every guest-facing statement of each kind is produced from its source: templates for transaction state, computed hours, inventory checks, retrieved items. Model prose that asserts one of them unbacked is discarded (authority guard).

---

## D-054 - timeout ≠ failure: SUBMISSION_UNKNOWN and reconciliation

**Decision.** When the provider's answer never arrives (timeout, or our worker crashed after the call), the booking may exist.

- **Providers that honour our `Idempotency-Key`** (`idempotent_submit: true`, the default): retry with the same key. That is safe: the provider returns the same booking. If retries run out after a timeout or crash, the status is **SUBMISSION_UNKNOWN**, never FAILED.
- **Providers without idempotency** (`idempotent_submit: false`): **no retry** (it could book twice). Immediately SUBMISSION_UNKNOWN.

In both cases:
- the guest hears "I can't confirm yet whether the booking was made, please don't book elsewhere";
- staff get a high-urgency RECONCILE handoff carrying the idempotency key.

If the provider supports `lookup(idempotency_key)` (`supports_lookup: true`), a `provider_reconcile` job settles the state automatically. A miss is not treated as proof: staff decide. Explicit 5xx and connection errors are still FAILED after retries.

SUBMISSION_UNKNOWN backs no claim, neither booked nor failed (authority tests). It is live (shown in the plan as "outcome unknown - being checked") and resolvable to SUBMITTED / ACCEPTED / PENDING_CONDITION / REJECTED / FAILED / CANCELLED.

**Evidence.** The first run of the new scenarios: the "provider without idempotency" case booked twice (2 external bookings). That was the most serious defect found this cycle.

---

## D-055 - Out-of-order and cross-provider callbacks

**Decision.**
- **COMPLETED from SUBMITTED is allowed:** completion implies the provider took the job. A later ACCEPTED is a backwards move: 409, no state change, no notification.
- **Callbacks are matched by (provider, reference).** A validly signed callback from provider A quoting provider B's reference is `unknown_reference` (404), and B's booking is untouched.
- **A duplicate event id is `duplicate`;** the same state with a new event id is `already_in_state`. Either way there is no second notification.
