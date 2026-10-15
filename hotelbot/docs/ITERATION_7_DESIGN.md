# Iteration 7 — Trip Intelligence / Journey Graph — DESIGN (for review, nothing implemented)

Status: proposal. No code, schema or fixture is created until this document is reviewed.
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

`TripItem.status ∈ {stated, booked, confirmed, changed, cancelled, unknown}`. Each state needs an observation of a specific kind:

| status | required evidence |
|---|---|
| `stated` | a guest statement only, no booking document |
| `booked` / `confirmed` | a provider document with a booking reference, e.g. "Booking confirmed, ref ABC123"; the parser records which phrase |
| `changed` | a provider document for the **same booking reference** that states a change (new time / segment) |
| `cancelled` | a provider document for the same reference that states cancellation |
| `unknown` | anything else; this is the default |

**Rules (hard gates):**
1. **No invented state.** A status comes only from an observation whose evidence span contains the status phrase. An LLM-proposed status without such a span is dropped.
2. **Never newest-wins blindly for status.** Status follows the precedence `cancelled > changed > confirmed > booked > stated`, *for the same booking reference and provider*, using the provider document's own issue time. An older "confirmed" never revives a "cancelled". A newer guest statement never downgrades a provider-confirmed booking: it is recorded as a contradiction (§5).
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
