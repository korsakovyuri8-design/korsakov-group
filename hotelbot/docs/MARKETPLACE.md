# Service marketplace (transport, rentals, guides)

**One conversational execution layer for the whole stay.** Any reasonable need becomes an ANSWER, an ACTION, a TRANSACTION or a HANDOFF. Every transaction is:

- discoverable, quotable and explicitly consented to;
- traceable and idempotent;
- status-authoritative;
- changeable or cancellable when the provider permits it.

```text
request ─► discovery (registry only) ─► candidates ─► hold inventory ─► QUOTE (+ material terms)
        ─► explicit consent (per offer, per sentence) ─► transaction ─► provider (submit / modify)
        ─► SUBMITTED ─► [PENDING_CONDITION] ─► ACCEPTED ─► IN_PROGRESS ─► COMPLETED
                                                 └► REJECTED / CANCELLED / FAILED (inventory released)
```

## Model

| Concept | Where | Notes |
|---|---|---|
| ServiceProvider | `external_providers` | shared (region) or a property's own; `profile`, `policies`, commission |
| ProviderPolicy | `external_providers.policies` + `offerings.policies` | merged into the quote's terms |
| ProviderLocation | `profile` (service area, locations) + offering `place` | |
| ServiceOffering | `offerings` | `service_type`, `pricing`, `attributes`, inventory |
| ServiceAvailability | `availability` (capacity) + `inventory_holds` (usage) | variants, windows |
| InventoryHold | `inventory_holds` | HELD (expires with the quote) → CONFIRMED → RELEASED |
| Quote | `quotes` | price, `terms`, `commercial`, `replaces_transaction_id` |
| ExternalTransaction | `external_transactions` | one per accepted quote; Action state machine |
| Property relationship | `property_providers` | preferred / default / exclusive / blocked |

## Demo providers (synthetic, `data/regions/zabljak_demo.yaml`)

| Provider | Family | Differs by |
|---|---|---|
| Demo Transfers | transport | cheap sedans, max 4 passengers / 4 bags, no child seats; hotel's DEFAULT |
| Demo Premium Transfers | transport | one minivan: 8 passengers, 10 bags, 2 child seats; dearer; supports in-place modification |
| Demo Ski & Board | rental | skis and snowboards by length; the Mali shop has 1 set per length, Savin Kuk more; winter only |
| Demo Bike & Trek | rental | bicycles (S/M/L) and e-bikes by half day, hiking and camping gear; bikes April-November |
| Demo Car Hire | rental | cars only: minimum 24 h, deposit 300 EUR, driver 21+, licence |
| Demo Peak Guides | guide | EN/ME; group hike (fixed weekend tour, per person) or private day guide; hotel's PREFERRED guides |
| Demo Durmitor Private Tours | guide | EN/RU; private Durmitor day, max 6, weather-dependent |
| Demo Culture Walks | guide | EN/DE; hourly cultural walk, minimum 2 h |

Nobody rents scooters or motorbikes: the bot says so.

## The demo conversation (`mkt_multi_transfer_skis_guide`)

```text
Guest: We arrive from Podgorica Friday evening. There are four of us. Get us a transfer, two sets of
       skis for Saturday, and I'd like a Russian-speaking guide for Durmitor on Sunday.
Bot:   plan - transfer: what time on 15.01? / skis: heights? / guide: Demo Durmitor Private Tours,
       17.01 09:00, 4 guests, RU - 180.00 EUR (code), depends on weather, ...
Guest: we land at 20:30            -> transfer 45.00 EUR (Demo Transfers sedan) + next question
Guest: 180 and 165                 -> skis 50.00 EUR (sized 180 + 170)
Guest: Book the transfer and the guide. I'll decide about the skis later.
Bot:   transfer + guide sent; "Still open, not booked: skis - 50.00 EUR - valid until ..."
       (async) transfer ACCEPTED; guide PROVISIONALLY accepted, subject to the weather
Guest: What do we have for Sunday?  -> "guide ... - provisional - subject to weather, not confirmed"
```

## Adding a vertical

1. **Data.** Add offerings in a region pack: `service_type`, `pricing`, `attributes` (unit, window, variants, requires, constraints), `policies`, and recurring or explicit capacity.
2. **Provider.** Declare it with `integration_type: mock|webhook` (see `PROVIDER_INTEGRATION.md`); `supports_modification` if it has a modify endpoint.
3. **Category or service.** A new rental category is one entry in `RENTAL_CATEGORIES`. A genuinely new service is one `ServiceSpec`.
4. **Property.** Enable the service in `capabilities.services`, and optionally add `provider_relationships`.
5. **Evals.** Encode its rules as scenarios.
