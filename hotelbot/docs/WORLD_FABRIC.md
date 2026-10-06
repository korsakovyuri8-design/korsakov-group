# World Data Fabric (Iteration 5)

The question this layer answers: **how does the system know what exists in a place, and which of that data to trust?** No single external platform owns the truth. Every source is evidence.

```text
 SOURCE A ─┐   adapters (transport only)        app/world/adapters.py
 SOURCE B ─┤          │
 PARTNER ──┤          ▼
 STAFF ────┤   NORMALIZE (per format)            app/world/normalize.py
 PROVIDER ─┘          │  raw kept (if licence allows), issues reported
                      ▼
               SOURCE ENTITY (one per source record, never deleted)
                      │
                      ▼
               ENTITY RESOLUTION (deterministic)  app/world/matching.py
                      │  MATCH / NO_MATCH / AMBIGUOUS (review)
                      ▼
               ENTITY LINK  ──►  CANONICAL ENTITY  (links are history)
                      │
                      ▼
               FACT ASSERTIONS (field level)     app/world/facts.py
                      │
                      ▼
               RESOLVER + POLICIES (config)      data/world/policies.yaml
                      │  value, winner, supporting, conflicting, state, reason
                      ▼
               PROJECTION: places / events rows  app/world/projection.py
                      │  resolution + quality + attribution per row
              ┌───────┴────────┐
              ▼                ▼
          DISCOVERY       TRANSACTIONS (via explicit identity links only)
```

## Canonical identity model

| Table | Meaning |
|---|---|
| `canonical_entities` | one real-world thing. Fields: `entity_type` (PLACE, EVENT, PROVIDER, VENUE), canonical name and slug, region (coverage area code), country, coordinates and geohash, `starts_at` (events), `active`, `deactivation_reason`, `merged_into_id` |
| `entity_links` | source entity → canonical entity, with `method` (new, auto_match, manual, split, correction), `score`, `evidence` (the rule and signals), `decided_by`, `ended_at`/`end_reason`. **Ended, never deleted.** |
| `entity_aliases` | every name each source uses (local, English, alternate, transliteration), folded for matching |
| `entity_identifiers` | normalized phone (E.164), web domain, explicit ids (`ext:<namespace>`), ticket URLs |
| `match_reviews` | AMBIGUOUS decisions waiting for a person; the records stay separate meanwhile |
| `entity_marketplace_links` | explicit identity between the worlds: entity ↔ provider / offering (set at ingest or by partner mapping) |

`places` / `events` rows are the **projection** of canonical entities. They are never written directly any more. Existing rows are adopted, so their ids stay stable for plan items and offerings.

## Source entity model

`source_entities` (unique per source and record id) hold:
- `source_id`, `source_type`, `source_record_id`, `source_url`;
- `raw`: the source-native payload, or nothing when the licence forbids caching; `raw_hash` always;
- `normalized` and `issues`;
- `first_seen_at`, `last_seen_at`, `last_synced_at`, `observed_at` (the source's own date);
- `active_at_source` and `tombstoned_at`.

`world_sources` holds each source's identity, authority class, format and licence terms. It also holds sync health: status, attempt and success timestamps, error, consecutive failures, records seen and changed, and cursor.

## Fact assertion model

`fact_assertions` hold one row per (source entity, field, value):
- `canonical_entity_id`, `source_id`, `source_class`;
- `observed_at`, `valid_from`, `valid_until` (temporary facts, e.g. a correction valid for a week);
- `confidence`, `verification_type` (source_reported, provider_owned, staff_verified, operator_verified, traveler_reported).

A changed value supersedes the source's previous assertion. A field the source stops sending is retracted. An unchanged value is re-observed. Every change is recorded in `world_changes`.

Fields include name, subcategory, coordinates, address, phone, website, timezone, price_range, opening_hours, kitchen_hours, temporary_closure, permanently_closed, description, tags and `attributes.*` (cuisine, diet, accessibility…). Event fields are title, event_category, start_at, end_at, venue, ticket_required, ticket_price, currency and event_availability.

## Entity resolution

The algorithm is deterministic and conservative; no LLM is involved. **A false merge is worse than a duplicate.**

**Blocking:**
- exact identifiers (explicit id, phone, domain, ticket URL);
- geohash cells within 300 m (places), or ±1 day (events).

Entities that already hold a record of the same source are never candidates.

**Names** are folded first (case, diacritics, Cyrillic → Latin, so Žabljak = Zabljak = Жабљак). Then:
- type words are mapped to classes ("konoba", "restaurant", "restoran" and "ресторан" all become one class; "pizzeria" is another);
- locality words are removed;
- tokens are fuzzy-matched, and the joined strings are compared.

A translated name alone never matches.

**Place rules:**

| Condition | Outcome |
|---|---|
| shared explicit identifier, nothing contradicting it | MATCH |
| shared explicit identifier, but contradicted (unrelated name without a shared contact, different kind, different phone) or more than 2 km apart | AMBIGUOUS |
| more than 300 m apart | NO_MATCH (chains: same brand elsewhere) |
| more than 300 m apart, **relocation**: same name, same current phone AND website, one location's evidence ≥ 90 days older, ≤ 50 km | MATCH (both locations current = chain = NO_MATCH) |
| different kind or category | NO_MATCH (AMBIGUOUS if they share a phone) |
| name ≥ 0.85 and ≤ 100 m | MATCH, but a different phone downgrades to AMBIGUOUS unless the name is ≥ 0.95 and they are ≤ 30 m apart |
| same contact at the same spot (≤ 50 m) | MATCH only with a compatible name (≥ 0.5) or TWO independent contacts (phone and website); otherwise AMBIGUOUS (a building's booking line) |
| same contact ≤ 200 m, name ≥ 0.5 | MATCH |
| name ≥ 0.6, ≤ 300 m | AMBIGUOUS |
| same spot, same kind, different names | AMBIGUOUS |

A contact is identity evidence only while fresh. A phone the other record last showed more than 365 days before ours does not count, because numbers are recycled.

Kind words never count as name content. Every single-word taxonomy keyword ("galerija", "apoteka", "museum"…) is a type token, so "Galerija Luna" and "Galerija Sunce" don't look alike.

**Event rules:**

| Condition | Outcome |
|---|---|
| shared explicit id or ticket URL | MATCH |
| more than 12 h apart | NO_MATCH (same artist, same venue, another date) |
| ≤ 15 min, same venue, title ≥ 0.8 | MATCH |
| ≤ 15 min, same organizer, title ≥ 0.6 | MATCH |
| ≤ 15 min, same venue, other title | AMBIGUOUS |

Two or more MATCH candidates → AMBIGUOUS.

**Evidence that arrives later.** A record re-sent with a new *explicit* identifier that another entity already carries is merged through the revertible merge. Nothing else re-links an existing record automatically.

## False-merge safety

- **Ambiguous cases stay separate.** They get a review; `identity.resolve_review` merges only on a person's decision.
- **Split, merge and revert.** `identity.split` (one record out into a new entity), `identity.merge` (the other entity stays with `merged_into_id`) and `identity.revert_merge` only end and create links and move assertions with their source entity. Source records and assertions are never destroyed. Plan items and offerings pointing at a merged-away place still resolve: the bridge follows `merged_into`.
- **Measured.** The benchmark and the eval gates measure false merges against ground truth: 0.

**Observation dates** reported in the future are clamped to the ingest time, and the skew is recorded (`future_observation_clamped`). A wrong clock never wins on recency.

## Field resolution policies

`data/world/policies.yaml` is data, not code. Per field it sets `fact_class` (freshness SLA), `risk` and an `authority` order. There is no global source priority. For example:

| Field | Authority (strongest first) |
|---|---|
| temporary_closure | provider_owned > staff_verified > operator_verified > official > partner_feed > tourism_feed > directory |
| coordinates | geo_verified > provider_owned > staff_verified > official > partner_feed > directory |
| start_at | organizer > ticketing_partner > official > … > directory |

**Resolver** (`facts.resolve`):
1. Only assertions valid now are considered.
2. Stale assertions step aside when fresh ones exist.
3. The strongest authority for this field wins.
4. At equal authority, the strictly more independent sources win; else a value at least 7 days newer wins; else CONFLICTED. A *high-risk* field that is CONFLICTED becomes **NEEDS_VERIFICATION with no value**.
5. A stale but stronger source that disagrees with the winner makes a high-risk field NEEDS_VERIFICATION. The weak source cannot override the authority, and the authority cannot be confirmed.
6. Every disagreement is kept in `conflicting`, never erased.

Output: value, winner, supporting, conflicting, confidence, freshness, state (resolved, contested, conflicted, needs_verification, unknown) and a human-readable reason.

## Conflict model

- **States:** contested (resolved by policy, with dissent recorded), conflicted (low-risk, newest shown and flagged) and needs_verification (high-risk, no value).
- **Projection:** a needs_verification field is *missing* from the projection. Hours become unknown; they are never one of the disputed values.
- **Discovery:** says "opening hours disputed — sources disagree about the opening hours, please check before going", and ranks such a place like stale data.
- **Winner ≠ certainty (contested high-dynamic facts).** Opening hours, kitchen hours, temporary closure, event start and event availability can be *contested*: a policy picked a winner, but some source disagrees. Such a winner never becomes a definitive traveller-facing claim. Discovery works out what the winner *and* each dissenting value would claim at the time asked (`engine.contested_claims`). When they differ, the reply gives both: "I have conflicting information about the opening hours: one source says open until 00:00, another says open until 23:00 - I can't confirm which is current". The place also ranks like stale data. Dissent that makes the same claim at that moment (it disagrees only about Sunday, say) is not a contest of the claim, so the claim is stated plainly. The contested resolution JSON carries the winner `value` next to its `conflicting` values so the renderer can show both. Gates: `world_newer_authoritative_wins`, `world_contested_majority_hours_not_definitive`, `world_contested_event_start_not_definitive`, plus the benchmark's `gate_contested_wrong_claim_stated_definitively` (must be 0).

## Freshness policies

These are per fact class (configurable):

| Fact class | Fresh up to | Aging up to |
|---|---|---|
| coordinates | 5 y | 10 y |
| identity | 2 y | 4 y |
| contact | 180 d | 365 d |
| static | 365 d | 730 d |
| prices | 60 d | 180 d |
| hours, kitchen | 30 d | 90 d |
| closure | 14 d | 30 d |
| event | 14 d | 45 d |
| event_time | 7 d | 30 d |
| event_availability | 1 d | 3 d |
| inventory (transaction world) | 5 min | 15 min |

The discovery engine's per-fact freshness reads the same configuration, using the observation date of the *winning* assertion.

## Source adapter v2

The contract (`SourceAdapter`):
- `descriptor`, with licence terms that must be declared before any data is used;
- `discover_scope(scope)`;
- `sync_full(scope, cursor)` and `sync_incremental(scope, cursor)`, both paged, with a cursor per page;
- `fetch_record(id)`;
- `health()`.

Adapters return source-native records; normalization is separate, selected by `format`. `V1Adapter` wraps any Iteration 4 WorldSource, which includes the synthetic region pack. `FixtureAdapter` is in-memory and versioned, with failure injection. No live connectors yet, by design.

## Normalization pipeline

Covers names (NFC, whitespace), phones (E.164 when the source country is known; never an invented country code), URLs and domains (platform domains such as facebook.com are not identity signals), coordinates (range check, 0,0 = missing), category mapping (per-source map; unknown → `unclassified` plus an issue), OSM-style opening hours (unspecified days closed; anything else is reported), price ranges, currencies, and event dates (to UTC; a missing timezone is reported, not assumed).

## Geo / spatial index design

- **Neutral grid.** `app/shared/geohash.py` provides encode, bounds, covering cells and prefix ranges.
- **Index.** `places.geohash` (precision 8) is indexed on both PostgreSQL and SQLite.
- **Repository.** `SpatialIndex` (`app/world/spatial.py`) has `within_radius`, `within_bbox` and `nearest` (expanding rings). Each scans the covering cells by B-tree range, then filters with exact haversine, so results are identical to a full scan (tested on random data).
- **Discovery.** `ProjectionRepository` (`app/discovery/repository.py`) uses the index for any query with an explicit radius.
- **PostGIS.** It can implement the same three calls (geography column, GiST index, `ST_DWithin`, KNN `<->`) where available. It is not required, so the test environment does not depend on it.

## Region / coverage model

`coverage_areas` has kinds country, admin_area, locality, bbox, radius and provider_area, each with a bbox or center plus radius, a timezone and a parent. An entity is assigned to the smallest containing area, whose code is the `region` discovery queries by. A sync targets any area (`Scope`). No country is hard-coded.

## Sync / job design

- **Pipeline.** `ingest.run_sync` is idempotent (an unchanged payload hash is a no-op apart from `last_seen_at`) and checkpointed (the cursor is stored after every page; `in_progress` survives a crash and the run resumes).
- **Failure.** A failure sets the source to FAILING with the error. Nothing known is removed or deactivated.
- **Tombstones.** Only after a *completed* full sync, and only at the source level.
- **Durable jobs.** On the existing PostgreSQL queue: `world_sync` (bounded retries with backoff; health is committed before the retry), `world_existence` (grace-period expiry) and `world_retention` (drops raw payloads past their licence retention).
- **Source health.** `ingest.source_health` reports HEALTHY, FAILING or STALE (no success within `max_age_hours`); stale is never shown as healthy.

## Existence

Missing from one sync ≠ closed. The entity stays active, and discovery says "no longer listed by its source - may have closed".

A canonical entity is deactivated only on evidence:
- a permanent closure asserted by an authoritative class (provider_owned, staff_verified, operator_verified, official); or
- every non-correction source has stopped listing it for the grace period (30 days).

It is reactivated when a source lists it again.

## Correction model

`corrections.submit` turns staff, provider, operator and traveller corrections into assertions from a correction source, with the authority class from `correction_classes`. They are audited in `world_corrections` and withdrawable; they never mutate source data.
- **Providers** may only correct entities they are explicitly linked to.
- **Traveller reports** carry an opaque random id, never a guest identity.

## License / attribution model

- **Declared terms.** Every source declares licence, `attribution_required` with `attribution_text`, `redistribution` (allowed, attribution, display_only or none), `retention_days` and `cache_raw`. A source without a licence is refused at registration.
- **Per result.** The projection records which winning sources require attribution (`resolution._attribution`), and replies that show such data add "Data: <attribution>".
- **Raw payloads** are not stored when caching is forbidden, and are dropped after the retention period. Normalized facts stay.
- **Snapshots** carry the licence metadata.
- **Term changes** are never silent. The previous terms go to `config.terms_history` with the time they ended, a `source_terms_changed` change is recorded, and the source's entities are re-projected. A value published under terms that required attribution keeps it, even if the source drops the requirement later.

## Snapshot model

- **Create.** `snapshots.create` freezes the evidence (sources with versions and licences, coverage, canonical rows, source entities, links, aliases, identifiers, reviews, assertions, corrections) as gzip JSON with a `content_hash` over the evidence.
- **Restore.** `snapshots.restore` requires an empty fabric and rebuilds the projection. Same evidence → same projection (tested).

## Data quality

`places.quality` stores components:
- identity confidence and whether it is under review;
- source authority per key field;
- freshness per dynamic fact;
- completeness and missing fields;
- conflicts;
- verification types;
- existence.

The `tier` (high, medium, low) is derived from them transparently. Discovery's reliability rank uses freshness, conflicts and existence, and `Candidate.components["quality"]` carries the tier.

## What is now required to add a new country

1. **Register coverage areas** for the country and its localities: `coverage.upsert_area`, data only.
2. **Register sources** with their licence terms and authority class: an official register, a tourism board, a licensed places dataset, partner and provider feeds, property staff (corrections already exist).
3. **Write one adapter per new source API** (the five-call contract), plus one normalizer per new record format. Map each source's categories onto the taxonomy, adding subcategories as data where needed.
4. **Tune policies by class of case**, not by fixture: a country whose official register is excellent may rank `official` higher for some fields.
5. **Schedule `world_sync` jobs** per source and area, plus `world_existence` and `world_retention`.
6. **Freeze a snapshot** and run the world, discovery and transaction gates against it.

No changes to discovery, transactions, the resolver or the schema.
