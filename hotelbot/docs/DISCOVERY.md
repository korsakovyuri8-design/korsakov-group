# Local World / Discovery Engine (Iteration 4)

The LOCAL WORLD is everything a traveller can find, compare, save or plan: places, venues and events. The TRANSACTION WORLD is what can be quoted, booked, changed or cancelled. They meet only in `app/marketplace/bridge.py`.

```text
WorldSource (synthetic pack | fixture | real feed later)
      │ normalized records + provenance
      ▼
app/world/store.sync ──► places / events  (deactivate, never delete)
                                 │
  guest words ─► NLU (UNDERSTAND) ─► DiscoveryQuery (hard | soft | time | where)
                                 │
          RETRIEVE ─► NORMALIZE ─► HARD FILTER ─► RANK ─► EXPLAIN
                                 │
       results ─► SAVE / SHORTLIST / PLAN (plan states)  |  "book" ─► bridge ─► quote + consent
```

## Domain objects

| Object | Where | Notes |
|---|---|---|
| `Place` | `places` | id, slug, name, category, subcategory, description, lat/lon, address, timezone, phone, website, price_range (1-4), attributes (JSON), hours (JSON), per-fact `verification`, provenance, active |
| `Event` | `events` | first-class: title, category (concert, festival, sports, market, exhibition, theater, nightlife, conference, local_event, other), start/end (UTC), optional place or own coordinates, ticket info (`ticketing: unknown` when the source says nothing), age limit, provenance, active |
| `TravelerPreference` | `traveler_preferences` | per guest, unique key; value, verbatim statement, source message |
| `ItineraryItem` | `itinerary_items` | SAVED / SHORTLISTED / PLANNED / PROPOSED / DISMISSED, or linked to a quote or action (status read from the source) |

Provenance on every place and event: `source_id`, `source_type`, `source_record_id`, `source` (label), `last_verified_at`, `confidence`, `provider_owned`, `is_synthetic`.

**Taxonomy.** `app/places/taxonomy.py` has 20 categories, including FOOD, NIGHTLIFE, CULTURE, ATTRACTION, NATURE, WELLNESS, SHOPPING, ESSENTIAL_SERVICE, HEALTH, CONNECTIVITY, FINANCIAL_SERVICE, MOBILITY_INFRASTRUCTURE and OTHER. There are 75+ subcategories with en/cnr/ru labels and keywords. A source can add subcategories as data (`taxonomy:` in a pack → `register_subcategory`).

## World sources

```python
class WorldSource(Protocol):
    source_id: str          # "synthetic:zabljak-demo"
    source_type: str        # synthetic | fixture | manual | partner_feed | api
    def region(self) -> RegionRecord: ...
    def places(self) -> Iterable[PlaceRecord]: ...
    def events(self) -> Iterable[EventRecord]: ...
    def taxonomy(self) -> Iterable[SubcategoryRecord]: ...
```

- `SyntheticRegionSource`: the region pack YAML. It converts local times to UTC and normalizes `price_range` (also read from `attributes.price_range`).
- `FixtureSource`: in-memory records.
- `store.sync(session, source)`:
  - upserts by (region, slug) and stamps provenance;
  - deactivates records the same source no longer lists;
  - maps unknown event categories to `other`.

## Pipeline (`app/discovery/engine.py`)

| Stage | Does | Never |
|---|---|---|
| UNDERSTAND (`nlu.py`) | words → `DiscoveryQuery`; compound messages → several queries (`understand`) | produces candidates |
| RETRIEVE | active places of the region by sub/category | reads offerings |
| NORMALIZE | local time in the place's timezone, venue state, food state, state at a later time, through a window, straight-line distance, walking estimate, per-fact freshness | asks a model for any of these |
| HARD FILTER | removes, with a counted reason | relaxes a constraint |
| RANK | lexicographic key in policy order (below) | lets commerce beat a guest criterion |
| EXPLAIN | reason and caveat codes, rendered by templates | invents a fact |

**Hard constraints.** Excluded kinds ("not a club"); required attributes (vegan, gluten-free, halal, kosher, wheelchair, child-friendly, pets, outdoor, Wi-Fi, takeaway, delivery); excluded attributes ("not formal" → dress code); `party_size` vs `max_group`; youngest age vs `age_restriction`; `price_max` ("cheap"); radius ("within 500 m", "10 min walk", "near the hotel" = 2 km); and, at an explicit time:
- known-closed or temporarily closed;
- kitchen closed;
- closes too early ("after midnight");
- closes before the window ends;
- the visit does not fit the window.

**Soft preferences.** "vegetarian if possible", lively/quiet, romantic, young crowd, locals, casual, nice/cheap value, stored traveller preferences.

**Ranking key (leisure):**
1. availability;
2. relevance;
3. preferences;
4. distance (0.5 km buckets);
5. reliability;
6. rating;
7. value;
8. relationship;
9. commission;
10. name.

**Ranking key (essential):** availability, distance (0.1 km buckets), reliability, name.

## Operating hours

`app/places/hours.py` supports weekly, kitchen, `last_entry`, seasonal, special (holiday) and temporary closure. States are OPEN / OPEN_LATER / CLOSED / UNKNOWN, with the next transition, in the place's timezone (falling back to the region's). `food_state()` is separate from venue state (D-058).

## Freshness

Per fact class: hours, kitchen, closure, prices, event, static (thresholds in D-058). STALE and UNKNOWN are disclosed in the reply. Future verification dates clamp to age 0.

## Geo

`app/shared/geo.py` is neutral: `Point`, haversine `distance_km`, a `TravelEstimator` protocol, and `StraightLineEstimator` (walking minutes from straight-line distance, always labelled "straight line"). Anchors:
- the property;
- the guest's location (when known);
- another place by name ("near the Black Lake", resolved from world data only; an unknown anchor is not guessed);
- the activity place of the day ("Saturday we're skiing" → the ski area, daytime only).

## Save / shortlist / plan

See D-059. Commands work per clause, by number, ordinal-within-kind, or name. Views:
- "my plan";
- "what do we have on Sunday?";
- "what are we doing tonight?" (17:00 onwards);
- "what did I save?";
- "show my saved bars".

**Free windows** (`app/trip/schedule.py`): "two free hours before dinner" takes the window from the plan (the dinner reservation). Without a plan item it counts from now and says so. Only places open through the window whose visit (`estimated_visit_minutes` plus walking both ways) fits are returned.

## Transaction bridge

- **"book the first restaurant"**: `bridge.options_for(place)` → a reservation quote that needs consent. A place without an offering → "can't be reserved through me" (walk-ins mentioned only when the data says so).
- **"buy 2 tickets for the concert"**: `bridge.options_for(event)` → an `event_tickets` quote scoped to that event (`_event_id`).
- **An event without a ticket offering:**
  - a free event: "needs no ticket";
  - a ticketed event: "not sold through me";
  - unknown ticketing: "not sold through me" (never "free").
- **"add the concert to Sunday"**: a PLANNED item. No tickets are bought.

## Outcome kinds

`AgentReply.outcomes` carries one or more of ANSWER, FIND, RECOMMEND, SAVE, ACTION, TRANSACTION, HANDOFF. The concierge records its own; the rest are derived from stored effects (offers made, staff actions submitted, hand-off).

## Replacing synthetic data with real global data

Nothing in discovery reads YAML. To go live:

1. **Write a `WorldSource` per feed** (partner CSV, a licensed places API, municipal open data, an events feed, property-curated lists). Map its fields to `PlaceRecord` / `EventRecord`. Set a stable `source_id` and `source_record_id`, `source_type`, the real `last_verified_at` per fact (`verification`), and a real `confidence`. Mark `is_synthetic: false`.
2. **Identity and merging.** Several sources describe the same venue, so a cross-source entity-resolution step (name + geo + phone/website) is needed to merge them. Field-level precedence is: provider-owned > verified partner > property-curated > open data. Today slugs are per region and source; one record per venue is assumed.
3. **Scheduling.** Run `sync()` per source on a schedule matched to the fact classes: hours and closures weekly, events daily, static data monthly. Deactivation-on-missing must be per source, as it is now.
4. **Hours quality.** Real feeds often lack kitchen hours, holidays and temporary closures. UNKNOWN must stay UNKNOWN; add a staff/partner correction channel that writes a `manual` source with its own verification dates.
5. **Licensing and attribution.** Store a licence/attribution label per `source_id` and render it. Respect feeds that forbid caching or limit retention.
6. **Geo.** Swap `StraightLineEstimator` for a routing `TravelEstimator` (walking or driving times) behind the same interface. Keep labelling the method used.
7. **Scale.** Retrieval is a SQL filter by region and category. For large regions, add a spatial index (PostGIS or a geohash column) and a candidate cap before NORMALIZE. Multi-timezone regions already work, because hours are evaluated in the place's timezone.
8. **Taxonomy mapping.** Map each source's categories to ours, and unknown ones to OTHER plus a review queue. Extend the taxonomy with `taxonomy:` records rather than code.
9. **Evaluation.** Freeze snapshots of real data for evals (as with the synthetic pack), and re-run the discovery gates on every source change.
