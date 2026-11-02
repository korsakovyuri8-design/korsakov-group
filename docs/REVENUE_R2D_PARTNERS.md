# Atlas R2D.3 — Partner booking and commission reconciliation

**Current verified reality:** outbound events in `revenue_clicks` include a confirmed Android Chrome test. This proves one persisted activation, not arrival at Kiwitaxi, a booking or any commission. No partner-confirmed bookings or commission reports have been provided.

Never create `confirmed_booking` rows from outbound clicks. The Kiwitaxi affiliate destination is a general URL, not a booking-event callback with stable identifiers. Without the partner's official conversion export or signed callback, neither per-click attribution nor client-side confirmation is defensible.

## Minimum partner report to request

For the **webmaster affiliate program**, Kiwitaxi says commission information appears in the **Reports** tab of the personal account (https://kiwitaxi.com/en/partner/webmaster/). Its public webmaster offer (https://kiwitaxi.ru/docs/terms_and_conditions_webmasters/) ties entitlement to fulfillment/use of the transfer, not merely a click or a booking. Therefore keep counts of partner-confirmed orders distinct from approved/accrued/paid commissions.

Obtain the official Kiwitaxi affiliate dashboard export or API documentation describing the reporting period, timezone, click count if available, verified bookings, cancellations/refunds, commission status (pending/approved/paid), currency, report identifier and attribution rules. Store raw partner exports separately under restricted access; do **not** paste guest names or booking details into Atlas telemetry.

Normalize manually into a **customer-free aggregate** CSV with columns:

```
provider,period_start,period_end,confirmed_bookings,approved_commission,currency,report_reference
```

One row per partner-reported period/currency; dates YYYY-MM-DD; `confirmed_bookings` nonnegative integer; `approved_commission` nonnegative numeric. Only use numbers supported by a verified partner report. `report_reference` should be an opaque report identifier, not a customer booking ID or personal name.

Run offline `python3 scripts/partner_reconciliation.py report.csv`. It will validate and total partner **reported** bookings/approved commissions by provider and currency; it does not query production PostgreSQL or assert per-click conversions. Keep the external partner report as audit evidence.

## Future integration gate

Before automating, obtain: authoritative Kiwitaxi partner documentation, permitted data scopes, authenticated reporting method, verified currency/payout rules, cancellation adjustments and callback signatures if supported. Implement an isolated provider adapter, reconcile report changes idempotently, test disputes/refunds and keep private booking details out of public Atlas DB telemetry. Do not infer conversion or commission from Atlas click IDs alone.
