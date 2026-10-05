# Local travel layer ("Trip / Stay OS")

The guest's journey: ANSWER → FIND → RECOMMEND → SAVE → BOOK → TRANSACT → CHANGE / CANCEL → HANDOFF.

```text
Region pack (YAML) ──► Place ──< Offering ──< AvailabilitySlot
                       Event          │
                                      └── ExternalProvider (region- or property-scoped)
Stay ──< ItineraryItem ──► Place | Event | Quote | Action   (status read from the source)
```

## Entities

| Table | What | Key fields |
|---|---|---|
| `places` | anything with a location and hours: restaurants, bars, pharmacies, ATMs, ski shops, museums, car parks... | `category` (19 values), `subcategory` (taxonomy code), `attributes` (JSON), `hours` (JSON), lat/lon, provenance |
| `events` | dated happenings (concerts, markets, DJ nights) | `start_at`/`end_at` (UTC), ticket info, optional `place_id`, tags |
| `offerings` | something bookable: a table, a ski set, a guide day | `service_type`, `provider_id`, `place_id?`, `attributes` (languages, max_party), price_from |
| `availability` | inventory slots | `starts_at`/`ends_at`, `capacity`, `remaining`, `attributes.unit` (people or group) |
| `itinerary_items` | the trip plan | SAVED / SHORTLISTED / PROPOSED / DISMISSED, or linked quote/action |

Provenance on every place/event/offering: `source`, `last_verified_at`, `confidence`, `provider_owned`, `is_synthetic`.

**Taxonomy** (`app/places/taxonomy.py`):

- ACCOMMODATION, FOOD, NIGHTLIFE, TRANSPORT, RENTAL, GUIDE, TOUR, ACTIVITY, ATTRACTION, CULTURE
- EVENT, SHOPPING, WELLNESS, ESSENTIAL_SERVICE, HEALTH, CONNECTIVITY, FINANCIAL_SERVICE, MOBILITY_INFRASTRUCTURE, OTHER

Each subcategory has localized labels (en / cnr / ru) and the keywords guests use for it.

## Hours

```yaml
hours:
  weekly:   {mon: [], tue: [["12:00", "23:00"]], fri: [["19:00", "02:00"]]}   # past midnight is fine
  kitchen:  {tue: [["12:00", "22:30"]]}                                         # "still serving"
  seasonal: [{from: "12-15", to: "04-10", weekly: {...}}]
  special:  {"2026-12-31": [["18:00", "03:00"]]}
  closed:   [{from: "2026-11-01", to: "2026-11-15", reason: "renovation"}]
  last_entry: "03:00"
```

`status_at()` returns OPEN (with closing time) / OPEN_LATER / CLOSED (with reason) / UNKNOWN. It is computed, never guessed.

## Discovery

`parse_discovery()` (deterministic, en/cnr/ru) → `DiscoveryQuery` → `discover_places()` / `discover_events()`:

1. **Retrieve** candidates by category/subcategory in the region.
2. **Filter** on hard constraints: exclusions, diet, accessibility, pets, open-at, kitchen-at, open-after-midnight, distance. A soft preference never yields its opposite.
3. **Rank** by preferences and tags, then distance. A commercial weight, if ever added, may only re-order what passed the filters.
4. **Explain** from fields only: hours status, kitchen, walking distance, matched attributes, caveats (reservation required, 18+, cover charge, stale data).

The property's knowledge pack answers first when it covers the same need ("Where is the parking?"), unless the guest asks about outside places.

## Plan and multi-part requests

- "save 2" makes a SAVED item.
- "book 1" on a venue starts a reservation (quote → consent → provider).
- "my plan" / "what are we doing on Saturday?" lists items, each with its own status.
- A multi-sentence message is split into independent items (see D-041); the guest confirms any subset: "book the transfer and the skis", "book all", or codes.

## Adding a vertical (no engine code)

1. **Data.** Add places / events / offerings with the right `category`, `subcategory` and attributes to a region pack. If a subcategory is missing, add one line to the taxonomy (labels and keywords).
2. **Provider.** Declare it in the region pack (or property pack) with `integration_type: mock|webhook` (see `PROVIDER_INTEGRATION.md`).
3. **Service.** If it is a new kind of transaction, add one `ServiceSpec` (fields, labels, keywords) to `app/transactions/catalog.py`.
4. **Capability.** Enable it per property: `capabilities.services.<service>: {provider: <slug>}`.
5. **Policy and evals.** Add the scenarios that encode its rules (capacity, language, age, hours).

The flagship example in `evals/scenarios/local.yaml` (transfer + dinner + skis + guide + drinks) uses exactly these pieces.
