# Connecting an external provider

HOTELBOT sells third-party services (transfers, taxis, tables, ski rental, guides...) through **providers**. The flow is always:

```text
guest request ─► QUOTE (price, conditions, valid_until, code)
              ─► explicit CONSENT (to that code)
              ─► TRANSACTION submitted via the job queue (idempotent, retried)
              ─► provider response / signed CALLBACK ─► guest notified from stored state
```

Payments are out of scope: nothing is charged by HOTELBOT.

## 1. Declare the provider

The provider can be scoped to a property (in the property pack) or to a region (in a region pack, shared by all properties in that region).

```yaml
providers:
  - slug: acme-transfers              # unique per property / region
    name: "ACME Transfers"
    provider_type: transport          # transport | restaurant | nightlife | ski_rental | guide | ...
    integration_type: webhook         # mock | webhook
    services: [airport_transfer, taxi]
    config:
      base_url: https://api.acme.example/hotelbot
      secret_env: ACME_SIGNING_SECRET        # we sign requests to the provider with this
      token_env: ACME_API_TOKEN              # optional bearer token
      callback_secret_env: ACME_CALLBACK_SECRET   # the provider signs callbacks with this
      timeout: 10
      estimated_duration_minutes: 150        # optional: transfer ETA used in trip plans
```

Then enable the service in the property's capabilities:

```yaml
capabilities:
  services:
    airport_transfer: {provider: acme-transfers}
```

Secrets are **never** written in packs. The schema rejects keys that look like secrets unless they end in `_env`.

## 2. HTTP contract (webhook adapter)

Every request from HOTELBOT carries:

```text
Content-Type: application/json
X-HotelBot-Timestamp: <unix seconds>
X-HotelBot-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
Authorization: Bearer <token>              (if token_env is set)
Idempotency-Key: <key>                     (bookings and cancellations)
```

| Call | Request body | Expected response |
|---|---|---|
| `POST /quotes` | `{service_type, details, locale}` | `200 {amount, currency, description, valid_until (ISO 8601), conditions?, reference?}` |
| `POST /bookings` | `{service_type, details, quote_reference, amount, currency, customer_reference}` | `200/201/202 {status: received\|accepted\|rejected, reference, message?}` |
| `POST /bookings/{reference}/cancel` | `{}` | `200 {status: cancelled\|rejected, message?}` |
| `GET /bookings/{reference}` | n/a | `200 {status, reference}` |

Rules:

- **Idempotency.** The same `Idempotency-Key` must return the same booking and never create a second one. HOTELBOT retries timeouts with the same key.
- **`customer_reference`** is an opaque stay id. No guest PII is sent unless the service needs it in `details`.
- **Error mapping:**
  - 5xx, 429, network errors, timeouts and malformed bodies are retried, with exponential backoff (5 s doubling to a 300 s cap) up to `max_attempts`.
  - 400/404/409/422 (invalid request) and 401/403 (auth) are permanent: the booking is marked FAILED, the guest is told honestly, and staff get a handoff.
- **`received`** means "we got it, not confirmed yet". The guest is told exactly that. A later callback moves it on.

## 3. Callbacks (provider → HOTELBOT)

```text
POST /api/providers/{property_slug}/{provider_slug}/callbacks
X-Provider-Timestamp: <unix seconds>
X-Provider-Signature: sha256=<hex HMAC-SHA256(callback secret, "<timestamp>.<raw body>")>

{"event_id": "evt-123", "reference": "<booking reference>", "event": "accepted", "message": "optional"}
```

`event` is one of `accepted`, `rejected`, `in_progress`, `completed`, `failed` or `cancelled`.

| Response | Meaning |
|---|---|
| 200 `applied` | state changed, guest notified |
| 200 `already_in_state` / `duplicate` | idempotent repeat, nothing changed |
| 400 | malformed payload / unknown event |
| 401 | bad signature, or timestamp outside the 300 s window |
| 404 `unknown_reference` | no booking with that reference |
| 409 `invalid_transition` | e.g. `accepted` after `completed`; nothing changed |
| 503 | callback secret not configured for this provider |

Send each event with a unique `event_id`. Retrying the same `event_id` is safe.

## 4. Inventory (optional)

If the provider shares availability, model it as `offerings` with `availability` or `recurring` slots in a region pack. Then HOTELBOT never quotes something the data says is full, and it offers the nearest real alternatives instead. Without modelled inventory, the provider's quote/booking response decides.

## 5. Checklist for a real provider

1. Agree the contract above (or write a small adapter class implementing `request_quote / submit / cancel / get_status` and register it in `ProviderRegistry`).
2. Exchange two secrets: outbound signing and callback signing. Put them in the environment.
3. Declare the provider and service in the pack; set `validity_minutes` / conditions as the provider defines them.
4. Run the transaction eval scenarios against a provider sandbox (`provider_config` overrides).
5. Enable it for one property. Watch `GET /api/staff/transactions` and `GET /api/staff/jobs?job_status=dead`.
