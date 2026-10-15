# Iteration 7 — Trip Intelligence / Journey Graph — DESIGN (for review, nothing implemented)

Status: **reviewed and accepted with corrections** (§13, decisions D-077–D-083). The corrections supersede the earlier text where they differ. Implementation proceeds in slices (§14).
Iteration 6.1 is closed (`ITERATION_6_1.md`). The Kotor/Budva pilot is not tuned further unless a defect blocks this iteration.

## 0. Goal and non-goals

**Goal.** One authoritative, normalised representation of a traveller's whole trip, built deterministically from trip facts that arrive through several inputs. Its chronological timeline, and the free windows derived from it, feed the existing Discovery Engine.

**Not in this iteration:**
- proactive rebooking or disruption handling (Iteration 8);
- live connectors: no Gmail, no booking APIs, no network;
- an LLM in the decision path. An extractor may propose candidate fields; code decides state.

## 1. What exists today (and is kept)

| existing | role today | in Iteration 7 |
|---|---|---|
| `Guest` | a person on a channel | becomes one possible identity of a **Traveler** (a traveller may also be a party member with no channel) |
| `Stay` (`status` INQUIRY/BOOKED/…, `booking_reference`, `arrival_at`, authoritative fields set only by staff/PMS) | the hotel's view of one visit at one Property | **unchanged**. A Stay is the property-side context. A trip's accommodation item may link to a Stay (`stay_id`), but the trip never writes Stay's authoritative fields |
| `ItineraryItem` / `PlanEntry` (status read from Quote/Action when linked) | the stay plan: saved / shortlisted / planned items, offers, bookings | stays the guest's *plan* layer. Trip items are *facts*, a separate layer. The timeline reads both, and each keeps its own owner |
| `Quote` / `Action` / `ExternalTransaction` (one state machine) | transactions Atlas itself made | **owner of transaction state, untouched**. A booking Atlas made appears in the trip by reference (`action_id`), never as a copy of its status |
| `schedule.free_window()` | a free window from words ("before dinner") and the stay plan | generalised: windows come from the **trip timeline**. The existing words-based path stays as one input |
| World Fabric (`CanonicalEntity`, `Place`, …) | world identity | trip items may **link** to world entities; the link never changes booking truth |

## 2. Domain model

The model mirrors the World Fabric split: **source record ≠ trip fact ≠ resolved trip item**.

```
Trip                       one journey of one party (id, title?, home_tz?, created_from)
 ├─ TripTraveler           party member: name as given, role (lead/companion/child), guest_id?, age_band?
 ├─ TripSourceRecord       one received input, immutable: kind, source_id, received_at, raw (verbatim), sha256
 │    └─ TripObservation   one field-level claim extracted from ONE record (item_key, field, value, evidence span)
 ├─ TripItem               resolved, one per real-world booking / segment / statement (see kinds)
 │    ├─ fields            resolved from observations (winner + all conflicting values kept)
 │    ├─ status            evidence-backed state (§4), never inferred
 │    ├─ links             world_entity (canonical id), stay_id, action_id / quote_id (Atlas-made bookings)
 │    └─ provenance        observation ids per field
 ├─ TripIssue              a surfaced problem: overlap, impossible connection, missing transfer, contradiction…
 └─ (derived, not stored)  Timeline = ordered TimelineEntries; TripWindows (free / travel / occupied)
```

**`TripItem.kind`** (one table, a typed `details` payload per kind):

| family | kinds | key fields |
|---|---|---|
| **JourneySegment** (moves the party) | `flight`, `train`, `bus`, `ferry`, `transfer` (taxi / shuttle / private), `drive` (self-drive leg) | origin, destination (with tz), departs_at, arrives_at, carrier, number, booking_ref, seats/class? |
| **Accommodation** | `accommodation` | property name/address, check_in_date/time, check_out_date/time, booking_ref, room?, guests |
| **Reservation** | `restaurant`, `activity` (tour / experience), `rental` (car / bike / boat), `event` (tickets) | venue, starts_at, ends_at?, party, booking_ref; rental: pickup/return place and time |
| **Statement** | `manual` (a guest statement: "we land Thursday around 2pm") | as above, with `source_class = guest_statement` |

A segment and a reservation are both TripItems. "Segment" and "reservation" are families inside the timeline, not separate tables. One table keeps idempotency, conflicts and provenance uniform.

### Time model (first-class time zones)

- Every instant is stored as **UTC plus the IANA zone of the place it happens**: `departs_at_utc + departs_tz`, `arrives_at_utc + arrives_tz`.
- A source's naive local time ("10:00 FCO") is converted with the zone **of that place**: the airport, station, port or venue. It is never converted with the reader's zone or the trip's zone.
- The zone comes from:
  1. the source (an explicit offset or zone);
  2. a static, frozen reference table (IATA airport → tz; station / port → tz);
  3. a linked World entity's timezone;
  4. if none of these, it is **unknown**. An instant with an unknown zone is kept as `local_naive` and flagged. It is never guessed and never placed on the absolute timeline as if known.
- Dates without a time (hotel check-in "18 Oct") are **date-only**. They do not become midnight. Policy defaults (check-in 14:00 / check-out 11:00) apply only as *assumed* bounds, which are flagged in the timeline as such.
- DST is handled by `zoneinfo`. The fixtures include a leg across the end of DST (last Sunday of October in Europe) and an ambiguous / non-existent local time.

## 3. Source records and observations

A **`TripSourceRecord`** has:
- `kind`: airline_email, hotel_email, restaurant_email, ticket (train / bus / ferry), rental_email, guest_statement, atlas_transaction;
- `source_id` (e.g. the Message-ID of a forwarded email, a fixture id, or a chat message id);
- `received_at`, the verbatim `raw`, `sha256(raw)`;
- `normalized_sha256`, a hash of the canonical text: headers stripped, whitespace and quoting normalised. It is used for idempotency (§6).

Records are immutable and never deleted.

**Extraction is per format and deterministic.** Each fixture family gets a parser. It emits **observations**: `(item_key, field, value, evidence = the exact span / line in raw)`.

An LLM extractor may be added later behind the same interface. It is a *proposer*: its observations carry `extractor = model`, they need a matching evidence span in the raw text, and they never set `status` by themselves (§4).

**Field resolution** is like the World Fabric resolver, but per TripItem:
- the observations of one field are kept **all**;
- the winner rule is explicit per field class;
- conflicts stay visible (`contested`);
- a field is never overwritten in place.

## 4. Status: evidence, not inference

`TripItem.status ∈ {stated, booked, confirmed, cancelled, unknown}` (D-081: a change is a **revision**, not a state). Each state needs an observation of a specific kind:

| status | required evidence |
|---|---|
| `stated` | a guest statement only, no booking document |
| `booked` / `confirmed` | a provider document with a booking reference, e.g. "Booking confirmed, ref ABC123"; the parser records which phrase |
| `cancelled` | a provider document for the same reference that states cancellation |
| `unknown` | anything else; this is the default |

**Rules (hard gates):**
1. **No invented state.** A status comes only from an observation whose evidence span contains the status phrase. An LLM-proposed status without such a span is dropped.
2. **Never newest-wins blindly for status.** For the same booking reference and provider: `cancelled` dominates every earlier or re-forwarded confirmation. Only explicit new evidence of reinstatement or rebooking (a provider document issued after the cancellation that says so) returns the item to `confirmed`. Otherwise `confirmed > booked > stated`. A provider "schedule changed" notice does not change the status: it creates a new **revision** of the item's fields (D-081). A guest statement never downgrades provider evidence; a disagreement is a `TripIssue` (§5, D-082).
3. **Atlas-made bookings.** If a TripItem links to an `Action`, its status is **read from the Action**, exactly as `ItineraryItem` does today. The trip layer never writes transaction state, and a confirmation email for an Atlas booking is an observation that must agree with it; disagreement is an issue.
4. **Payments.** "Paid" is never a trip status. A payment claim is a field observation (`payment_claim`) with evidence and is shown hedged. Payment truth belongs to the transaction layer.

## 5. Conflict classes (all surfaced as `TripIssue`, none auto-fixed)

| class | detection | handling |
|---|---|---|
| `duplicate_confirmation` | same provider + booking ref + same resolved fields from another record | merged into one TripItem; the record is linked as a duplicate, no issue shown |
| `updated_booking` | same provider + booking ref, a newer provider document with different fields | a new observation set: fields resolved by the provider document's issue time; old values kept; status `changed` only if the document says so |
| `contradictory_times` | two sources disagree on a field of the same item and neither is an explicit update | field `contested`; issue shown; the timeline uses neither as certain |
| `overlapping_bookings` | two occupying items (stay / segment / reservation) overlap for the same traveller | issue; both kept |
| `impossible_connection` | arrival of segment A after departure of segment B, or less than the minimum connection time (configurable per mode) | issue |
| `missing_transfer` | consecutive items at different places (> configurable distance) with no segment between them | warning with the gap and both places |
| `cancelled_vs_confirmed` | a cancellation and a confirmation for the same reference | status `cancelled` (precedence); the older confirmation is kept as history; issue if the cancellation predates the confirmation's issue time |
| `unknown_status` | an item with no status evidence | shown as "not confirmed" |
| `unknown_timezone` | a time with no resolvable zone | the item stays off the absolute timeline (`floating`); issue |
| `assumed_bounds` | check-in / check-out times from policy defaults | flag (info), not a conflict |

## 6. Idempotency and item identity

- **Record level.** The same `normalized_sha256` → the same record. It is not stored twice; it is linked as a re-delivery.
- **Item level.** The item key is `(provider_normalised, booking_ref)` when present, plus a segment discriminator (flight number + date of departure) for multi-segment bookings.
- **Without a booking ref** (a guest statement), the key is `(kind, normalised place, local date)`. A statement plus a later provider document for the same kind/place/date is **linked**, not merged: the document upgrades the evidence, and the statement stays as an observation.
- **Tests cover:**
  - identical input;
  - a forwarded copy (Fwd: header, quoted `>` lines);
  - a formatting change (whitespace, HTML → text);
  - an update to an existing booking (same ref, new time);
  - a second segment under the same ref.

## 7. World linking (identity ≠ truth)

A TripItem may link to a canonical World entity: airport, hotel / property, restaurant, station, port, attraction, provider.
- Linking uses deterministic keys first: IATA code, an explicit place id, a booking ref → `Action` → Offering → Place. Name + geo is a **proposal** under the 5.1 rules.
- A missing or ambiguous match never deletes, hides or downgrades a booking. `world_link = none` is a valid state.
- Linking never writes TripItem fields. It may add *display* data (opening hours of the restaurant, coordinates for transfer distance), always labelled as World data, and the booking's own stated time always wins over World opening hours. A conflict between the two is an issue (e.g. a reservation at 23:00 at a place World says closes at 22:00).

## 8. Timeline engine and windows

The timeline is a deterministic function: `timeline(trip, policies) -> [TimelineEntry]`, ordered by `(start_utc, end_utc, kind_rank, item_id)`.

- **Occupied intervals:** segments (departs → arrives), reservations (starts → ends, or a per-kind default duration that is flagged as assumed), and accommodation as the "base" for each night.
- **Travel buffers (policy, configurable):**
  - airport arrival → kerb, e.g. 45 min after landing;
  - hotel check-in handling;
  - minimum connection time per mode;
  - transfer time from World distance or a stated transfer.
- **Free windows:** the gaps between consecutive occupied intervals, after buffers and within *waking hours* (configurable, e.g. 08:00–23:00 local). Each window carries the place it starts at, its local zone and the reason it ends.
  - Example: landing 14:10 TGD (+45 min) → transfer 15:00–16:00 → check-in 16:00 (+30 min) → dinner 20:00 at the restaurant (−30 min travel). The free window is **16:30–19:30**, anchored at the hotel and closed by the dinner.
- **Discovery bridge:** a free window becomes a `DiscoveryQuery` with `at = window.start`, `window_end = window.end` (the existing "open throughout" / "fits the window" logic), `near = window anchor`, and the window's zone.
  - No new ranking: the time intent is explicit, so the Iteration 6.1 policy applies (availability first).
- **Never silently fixed:** overlaps, impossible connections, missing transfers and contested times are entries in `TripIssue`. The timeline shows the items as received.

## 9. Invariants (each a hard test gate)

1. **No invented booking state** (§4.1). An extractor's status without an evidence span is rejected.
2. **Provenance.** Every resolved field points to ≥ 1 observation, and every observation to a record with raw evidence.
3. **Idempotent import** (§6). Re-importing any fixture changes nothing: same items, same hash of the trip state.
4. **Time-zone correctness.** Instants are UTC plus the zone of their place. Cross-zone and DST fixtures produce the expected UTC and local values.
5. **Deterministic timeline.** Same records in any import order → the same timeline and windows, byte-identical.
6. **Conflict preservation.** No observation is deleted or overwritten. Every contested field keeps all values.
7. **Free-window derivation.** The expected windows for the fixture trip, with buffers.
8. **World linking cannot change booking truth.** Removing, adding or changing a World link leaves status, times and fields identical.
9. **Transaction state stays owned by the transaction layer.** The trip layer has no code path that writes `Quote`, `Action` or `ExternalTransaction`, nor `Stay`'s authoritative fields. This is checked by an architecture test, like `test_architecture.py`.
10. **Unknown stays unknown.** A missing zone, time or status is never defaulted silently. Assumed bounds are flagged.

## 10. Fixture set (frozen, offline)

Fixtures are in `data/trips/fixtures/`. Each has a raw input plus an expected-observations file. All names, references and contacts are fictitious.

| # | fixture | kind | what it exercises |
|---|---|---|---|
| F1 | `airline_lgw_tgd.eml` | airline e-mail | flight LGW → TGD, local times + IATA zones (Europe/London → Europe/Podgorica), booking ref, 2 passengers |
| F1b | `airline_lgw_tgd_fwd.eml` | forwarded copy | the same booking with a `Fwd:` header and quoted lines → **no duplicate** |
| F1c | `airline_lgw_tgd_change.eml` | change notice | same ref, departure moved +2h → `changed`, old time kept |
| F2 | `hotel_kotor.eml` | hotel confirmation | check-in / check-out dates, check-in time "from 14:00", confirmation number |
| F3 | `restaurant_kotor.txt` | restaurant reservation | 20:00, party 2, reservation code |
| F4 | `ferry_or_bus_kotor_dubrovnik.pdf.txt` | ticket (bus / ferry) | cross-border, Europe/Podgorica → Europe/Zagreb (same offset; the zone must still be per place) |
| F4b | `train_dst.txt` | train ticket | a leg across the end of DST, plus a non-existent local time |
| F5 | `rental_dubrovnik.eml` | car rental | pickup / return place and time, self-drive leg |
| F6 | `guest_statement.json` | manual statement | "we land Thursday around 2pm" → `stated`, linked (not merged) with F1 |
| F7 | `activity_kotor_tour.eml` | activity booking | a tour next morning |
| F8 | `cancel_restaurant.txt` | cancellation | cancels F3 → `cancelled`; F3 re-imported later does not revive it |
| F9 | `hotel_kotor_dup_html.html` | the same hotel booking as HTML | formatting change → **no duplicate** |
| F10 | `overlap_second_hotel.eml` | a second hotel, same nights | `overlapping_bookings` issue |
| F11 | `llm_proposed_status.json` | extractor output | a proposed "confirmed" with no evidence span → **rejected** (gate 1) |

**End-to-end demo trip (London → Podgorica → Kotor → Dubrovnik).** F1 + F2 + F3 + F7 + F4, with **no transfer** between TGD and the Kotor hotel. Expected outputs:
1. the normalised trip;
2. the chronological itinerary (local times per place);
3. the free windows (e.g. 16:30–19:30 on arrival day, morning before the tour);
4. **one `missing_transfer` warning** (TGD airport → Kotor hotel, ~20 km);
5. Discovery suggestions for one free window, on a small frozen World fixture: a few Kotor places with hours. The Kotor/Budva pilot world is not modified.

## 11. Gates for "done"

- All existing tests still pass (currently 862 passed / 2 skipped).
- New hard gates for invariants 1–10, each a named test.
- The E2E fixture trip produces exactly the expected normalised trip, timeline, windows, one missing-transfer warning, and a non-empty, window-fitting Discovery result. Snapshot-compared, deterministic.
- An architecture test: the trip module imports nothing that writes transaction or Stay authoritative state.

## 12. Decisions for review

1. **Separate `Trip` above `Stay`, rather than generalising `Stay` into a trip.** Recommended: Stay stays the property's authoritative view (PMS / staff); a Trip may reference several Stays.
2. **One `TripItem` table with kind-typed `details`, or a table per family.** Recommended: one table, for uniform idempotency, provenance and conflicts.
3. **Where the IATA / station / port → tz reference table comes from.** Recommended: a small frozen CSV in the repo, under a licence-clean source (OurAirports is public domain). A World link is the second source.
4. **Default buffers and waking hours** as policy (`data/trips/policies.yaml`). Initial values are proposed in the fixture review.
5. **Status precedence order** (§4.2). In particular, whether `changed` outranks `confirmed` from a *later* re-confirmation of the changed booking. Proposed: same reference, and the latest provider document of kind change / confirm wins *between those two*; `cancelled` always dominates unless a later provider document explicitly reinstates the booking.
6. **Guest statement versus a provider document disagreeing on time.** Proposed: the provider value is the winner; the statement is kept; an issue is shown only when the difference exceeds 1 h.
7. **Scope of the E2E Discovery step.** Proposed: a small frozen World fixture, not the Kotor/Budva pilot world, to keep this iteration independent of 6.1 data.

Decision ids D-077 onward are assigned when these are accepted.


## 13. Accepted review decisions (supersede the text above where they differ)

- **D-077: a Trip is a separate entity above Stay.** Stay stays the property / PMS context of one stay. The relation is Trip ↔ Stay only: the trip layer never writes Stay's authoritative fields.

- **D-078: one `TripItem` table with a common core.**
  - The core columns are kind, start/end time records, status, provider, booking_reference, world_entity_id and provenance.
  - Kind-specific fields go in a typed `details` JSON. Each kind has a validator; unknown keys and wrong types are rejected.
  - There are no per-kind nullable columns.

- **D-079: time-zone registry.**
  - **Airports:** a frozen, dated snapshot of OurAirports (public domain), IATA → IANA zone.
  - **Stations and ports:** a small, explicitly versioned registry of the fixtures' own stations and ports.
  - There is no global station database in this iteration. A deterministic coordinate → IANA resolver may be added later.
  - **Invariant:** a zone that is not proven stays UNKNOWN. The user's device zone is never used as a fact's zone.

- **D-080: policies are versioned and split into hard and soft** (`data/trips/policies.yaml`).
  - **Hard (operational):** minimum connection times, travel buffers, check-in / check-out handling.
  - **Soft (traveller):** waking hours, e.g. "no museum at 06:00". The user may override these.
  - Every derived window records the policy version and the values it used in its provenance.

- **D-081: "changed" is a revision, not a lifecycle state.**
  - **States:** STATED / BOOKED / CONFIRMED / CANCELLED / UNKNOWN.
  - **Revisions:** a provider change notice for the same booking creates a new revision of the item's field values, linked to its evidence. The status (e.g. CONFIRMED) is unchanged, and earlier revisions are kept.
  - **Cancellation:** CANCELLED dominates older confirmations. Only explicit, newer reinstatement or rebooking evidence brings the item back, never a re-forwarded old confirmation.

- **D-082: provider evidence is authoritative for provider-owned facts.**
  - These are times, segments, status, cancellation and the booking reference. A guest statement is kept but does not override them.
  - **Disagreement:** a `TripIssue`, with fact-specific tolerances in policy. For example, transport times have a strict tolerance, while a guest's approximate hotel arrival has a loose one.
  - **Status:** there is no tolerance. "Guest says confirmed" against "provider says cancelled" is an issue immediately.

- **D-083: a separate, small fixture world for the E2E demo.** It is not the Kotor/Budva pilot world. The real World Fabric is connected later, in a separate integration test.

**Additions accepted before the first migration.**

- **Two-level idempotency.**
  - `TripSourceRecord` keeps the raw SHA256: proof that this exact document was seen. A forwarded copy has a different raw hash and creates a **new source record**.
  - The `TripItem` is found by its **semantic identity**: provider + booking reference + segment identity (carrier + number + scheduled local departure date). So the forwarded copy adds observations to the existing item and never creates a second one.

- **A time record, not just UTC.** Every authoritative time is stored as `{raw, local (wall time), tz (IANA), utc, evidence}`:
  - `raw` is the text as written;
  - `tz` is null when it is not proven;
  - `utc` is null unless `tz` is proven;
  - `evidence` is the observation id.

  An **ambiguous local time** (the DST fall-back hour) or a **non-existent** one (spring-forward) produces a `TripIssue` and no UTC. Nothing is silently chosen with `fold=0/1`.

## 14. Implementation slices

1. **Slice 1 (narrow).**
   - Build `Trip`, `TripSourceRecord`, `TripObservation`, `TripItem` (with revisions), the migration, and the airline confirmation parser for fixture F1.
   - Prove four things:
     - provenance;
     - time zones (London → Podgorica, with the time record);
     - idempotent re-import (identical file, forwarded copy, formatting change);
     - an update as a revision (F1c), with the status unchanged.
   - Nothing else is built until this is green.
2. **Slice 2.** Hotel, restaurant and cancellation fixtures (F2, F3, F8, F9), with status precedence and provider-vs-guest issues (F6).
3. **Slice 3.** Timeline, windows, policies, overlaps, missing transfer; then the E2E fixture world and the Discovery bridge.

## 15. Slice 1 — done (Trip / records / observations / items; airline F1)

**Commits.**
- f4f0e5e — fixtures and failing tests.
- 2e4751d — more gates, and a migration test for 0009.
- ec2c47f — the implementation.

**What exists.**
- **Models and migration `0009_journey`:** `Trip`, `TripSourceRecord`, `TripItem`, `TripObservation`, `TripIssue`.
- **`app/journey/timeinfo.py`:** the time record (raw → local → IANA zone → UTC → evidence, plus the registry version).
- **`app/journey/parsers/airline.py`:** a deterministic parser for F1.
- **`app/journey/ingest.py`:** import, revisions and model proposals.

**Fixtures** (`data/trips/fixtures/`):

| fixture | what it is |
|---|---|
| F1 | the confirmation |
| F1b | a forwarded copy with `> ` quoting and another Message-ID |
| F1d | a re-sent copy with different spacing and headers |
| F1c | a schedule-change notice: same booking, departure +2 h, "remains confirmed" |

All names and contacts are fictitious; the carrier is "Demo Air", code DM.

**Gates (17 tests in `tests/test_journey_slice1.py`, all green).**
- **One flight item.** F1 gives a single `flight` item, `confirmed`, with validated details.
- **Provenance.** Every resolved field has an observation whose evidence text literally occurs in the raw document. The status evidence contains "confirmed".
- **Time zones.** LGW 09:15 → `Europe/London`, 08:15 UTC (BST). TGD 12:55 → `Europe/Podgorica`, 10:55 UTC (CEST). Every record carries raw, local, tz, utc, evidence and registry.
- **Unknown zone.** An airport not in the registry → `tz=None`, `utc=None`, plus an `unknown_timezone` issue.
- **DST.** The ambiguous London time 2026-10-25 01:30 and the non-existent 2026-03-29 01:30 both give an issue and no UTC.
- **Idempotency.** The identical file again changes nothing. F1b and F1d each add a new record but no new item and no new revision.
- **Revision.** F1c is revision 2: new times, status still `confirmed`. Revision 1 is kept, and `changed_fields = [arrives, departs]`.
- **An unstated field is not a change.** F1c lists no passengers, and the passengers stay.
- **Issue order wins over arrival order.** F1c imported before F1 still stays current, because the provider issued it later.
- **No invented state.** A model-proposed status is rejected, with or without evidence. Other proposals need evidence that literally occurs in the raw.
- **Validated details.** Unknown keys and wrong types are rejected.
- **Ownership.** `app/journey` imports nothing from transactions, marketplace or stays, and does not name `Quote`, `Action`, `ExternalTransaction` or `Stay`.

**Deviations, recorded.**
1. **D-079 time-zone registry.** OurAirports' `airports.csv` has coordinates but **no time-zone column**, and this environment has no network. Slice 1 therefore uses a small, explicitly versioned, hand-curated registry (`data/trips/tz_registry.yaml`, version `2026-10-15.1`) covering the fixture airports. A full airport snapshot needs a coordinate → IANA resolver: a separate, later step.
2. **Migration test.** The world-fabric migration test assumed 0008 was the head. It now compares the models at `head`, the convention the 0007 test already used. Its data assertions are unchanged.
