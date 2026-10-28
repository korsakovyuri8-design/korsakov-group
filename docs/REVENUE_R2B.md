# Revenue R2B candidate — NOT LIVE

Base: `a86d3131014c5d90a10316ad6b4dd95ea66d48af`, branch
`ccr-b496a55d-mr7ho5`. Candidate branch:
`atlas/revenue-r2b-measurement-route-catalog`. This document describes a development
candidate, not a production acceptance. No production migration, service change,
release switch or partner transaction is authorized by this implementation.

## Behavior and boundaries

`POST /revenue/click` records an **outbound activation only**. It does not prove
arrival at the partner, coverage, a quote, booking, provider acceptance, payment,
commission, or unique human traffic. Events are best effort and may be lost when
the browser, storage, network, rate limiter or database is unavailable. Repeated
user activations are separate events. There is no page-view denominator or CTR.

`RevenueClick` / `0013_revenue_clicks` adds one table and two indexes, with provider
and placement check constraints. IDs and UTC timestamps come from the server.
The table has no foreign keys, private session identifiers or relationships to
journeys; demo reset and TTL cleanup cannot cascade into it. The application
exposes insert only, no update/delete/read analytics API. Administrative DB access
can still modify rows: this is not a tamper-proof audit ledger.

No IP, user agent, name, phone, email, raw URL/query/referrer, destination URL,
booking state or commission field is persisted. SQL errors and invalid payloads
are not logged or echoed. Uvicorn access records for the endpoint are suppressed;
the nginx candidate also disables endpoint request/error logs and strips cookie,
authorization, referrer, user-agent and client-address forwarding headers.
Production must apply the corresponding location to the real TLS virtual host.

Validation / responses:

| Condition | HTTP |
| --- | --- |
| One row committed | 201, `{"ok":true,"id":"<server UUID>"}` |
| Malformed/duplicate-key JSON, query parameters, invalid content length | 400 |
| Browser cross-origin or same-site-but-other-origin write | 403 |
| GET/PUT/PATCH/DELETE (no read or edit API) | 405 |
| Body above 2,048 bytes, including streamed bodies | 413 |
| Non-JSON or compressed request body | 415 |
| Unknown field, provider, placement, slug, UTM label, missing landing slug | 422 |
| Global budget exceeded | 429, application `Retry-After: 60` |
| Database error | 503, no claimed success and no automatic retry |

Only canonical lower-case catalog slugs are accepted; unknown or non-normalized
slugs are rejected, not silently reattributed. `route_slug` is required for
`route_landing`; other placements can omit it. Missing-transport links do not
invent a catalog route from private or ambiguous trip text.

The application limit is 60 requests per rolling minute **per process**, including
invalid requests. It retains at most 60 timestamps, no visitor identity. The
nginx example adds a shared server-wide 60/minute leaky bucket with burst 10.
This deliberately trades some measurement completeness for a small, bounded
anonymous endpoint. Reassess capacity before traffic grows. It is not bot proof.
Do not trust arbitrary X-Forwarded-For headers or add fingerprints for analytics.

### Attribution policy

Only reviewed, non-personal campaign labels are accepted. A character/length
filter alone would still permit names or phone numbers. Client sanitization
trims/lowercases recognized labels and drops everything else; the server strictly
rejects invalid values. Maximum value length is 48 characters.

- Sources: instagram, tiktok, youtube, threads, google, bing, linkedin, facebook,
  newsletter, direct.
- Mediums: organic, social, shorts, email, cpc, referral.
- Campaigns: montenegro_launch, uzbekistan_launch, revenue_r2b, rollout_smoke.
- Contents: `(reel|post|story|short|video|link)_[0-9]{1,3}`.
- Terms: airport_transfer, private_transfer, montenegro_transfer, uzbekistan_transfer.

Only these labels are kept in `sessionStorage` under
`atlas.revenue.attribution.v1`, within the current tab. The last explicitly tagged
landing replaces prior attribution; an invalid tagged landing clears it. Untagged
internal navigation retains the sanitized labels. There is no visitor ID or
cookie, and no private Atlas session token is read. Blocked sessionStorage does
not break the CTA. Attribution does not assert a real acquisition source.

To add an owned campaign, review its non-personal vocabulary and update both
`atlas-frontend/src/lib/revenue/attributionPolicy.json` and
`hotelbot/app/atlas_web/revenue.py`; a parity test rejects drift. Likewise, the
backend slug registry is checked against the single frontend content catalog.
This keeps standalone backend and frontend release packaging independent.

Accepted example:

```json
{
  "provider": "kiwitaxi",
  "placement": "route_landing",
  "route_slug": "podgorica-kotor",
  "utm_source": "instagram",
  "utm_medium": "organic",
  "utm_campaign": "montenegro_launch",
  "utm_content": "reel_01"
}
```

Also accepted: `{"provider":"kiwitaxi","placement":"missing_transport"}`.
Rejected: either example plus `"session":"..."`, `"commission":10`, an unknown
route, `"utm_campaign":"john_smith"`, or `"utm_source":"guest@example.com"`.

### Frontend and SEO

`RevenueOutboundLink` preserves the configured HTTPS href, new-tab behavior and
`noopener noreferrer`; only affiliate links also use `sponsored`. Primary click,
keyboard-generated click and middle-click send one beacon, falling back to one
keepalive fetch only if beacon queueing fails. Hover, focus, right-click and
impressions send nothing. No handler prevents navigation, awaits telemetry,
retries an uncertain write, or changes authoritative trip state.

All three placements use this component. Build-time `__ATLAS_REVENUE__` shared by
SSR/client is preserved. There are no new partner URLs or synthesized deep links.
Kiwitaxi is the only currently approved production configuration. Other providers
remain hidden unless an operator explicitly configures an approved HTTPS URL.

`src/lib/revenue/transferRoutes.json` contains exactly nine English guides with
specific planning advice, country/origin/destination, checklists and reciprocal
links. A single `transfer.$slug.tsx` serves them; the original Podgorica–Kotor URL
is unchanged. Each has SSR h1/title/description, canonical URL, OG/Twitter tags,
WebPage JSON-LD, conditional CTA and disclosure. No Offer, ratings, fares,
availability, duration promises or confirmed-booking claims are synthesized.
All nine currently contain unique useful copy and are indexable. Setting an
entry's `indexable` to false produces noindex metadata and omits it from sitemap.

Unknown slugs are HTTP 404 with `X-Robots-Tag: noindex`. Public pages bypass both
ClientOnly and the private root loader in SSR and browser navigation.
`/sitemap.xml` is deterministic XML served in the actual Nitro server entry
before the private router; it includes the homepage and indexable routes.
`public/robots.txt` points to it. Repository-root legacy website robots/sitemap
files are unrelated and were not changed.

## Development verification commands

Use isolated development credentials only. The PostgreSQL test fixtures erase
tables in `HOTELBOT_TEST_DATABASE_URL`; **never point this at production, staging,
or any shared database**. Do not source `/etc/atlas/atlas.env` for tests.

```sh
cd hotelbot
python -m pytest
# Fresh dedicated disposable PostgreSQL DB only:
HOTELBOT_TEST_DATABASE_URL='<isolated test URL>' python -m pytest tests/test_revenue_clicks.py tests/test_migrations.py
cd ../atlas-frontend
bun install --frozen-lockfile
bun run test
bunx tsc --noEmit
bun run build
```

Test builds use a dummy affiliate identifier, not production credentials. Build
once with affiliate configuration absent and once with a test HTTPS Kiwitaxi URL,
then run the raw smoke on each isolated node-server process:

```sh
HOST=127.0.0.1 PORT=18092 node .output/server/index.mjs
# In another shell, choose the matching mode:
python3 scripts/smoke-revenue.py http://127.0.0.1:18092
python3 scripts/smoke-revenue.py http://127.0.0.1:18092 --affiliate
```

Do not run the two smoke modes against the same build. The script performs reads
only and checks all nine raw SSR bodies, metadata, structured data, affiliate
state, reciprocal links, homepage, unknown 404, sitemap/HEAD and robots.
See `REVENUE_R2B_VERIFICATION.md` for actual executed results.

## Operator-reviewed production rollout — separate approval required

These are instructions, not actions performed by this task. Adapt them to the
real service units, nginx includes, TLS virtual host and deployment paths after
inspection. Do not blindly execute the old `deploy-atomic.sh`: its current smoke
checks only the homepage and it does not orchestrate the backend migration.

### 1. Freeze and identify the candidate

- Approve/review the PR into `ccr-b496a55d-mr7ho5`; never deploy from `main`.
- Inspect `git status`, source branch/HEAD and remote HEAD in `/opt/atlas`.
  Require a clean tree and an operator-recorded exact approved candidate SHA.
  The development base was `a86d3131014c5d90a10316ad6b4dd95ea66d48af`; if production
  or remote has moved, reconcile explicitly. No reset/clean/force operations.
- Capture `systemctl show atlas atlas-frontend -p MainPID -p ExecStart`, service
  health and current frontend symlink. Keep the old release and service/config
  copies. Read sensitive configuration locally; never print its contents.
- Prepare an isolated worktree at the approved SHA. Pin/reuse the validated
  dependency versions. Run tests/build serially on the small production host;
  preferably perform the expensive verification on an isolated build machine.
- Check `nginx -t` against the candidate configuration and inspect actual TLS
  host routing. Add the new `limit_req_zone` once in `http`, and the exact
  `/revenue/click` location in the TLS server. Preserve all existing demo routes.
  Use the proposed explicit headers, without an additional `proxy_params`
  include in this location. Trust forwarded protocol only from local nginx
  (`uvicorn --proxy-headers --forwarded-allow-ips=127.0.0.1`, adjusted if needed).
  Otherwise the application's Origin check can reject HTTPS browser writes.

### 2. Back up and verify database invariants

- Create a protected PostgreSQL custom-format backup of `atlas_prod` and test
  restoring it to an **isolated** DB before changing production. Record backup
  location, timestamp and checksum in the private operator record, not public
  logs. Use local protected credentials/.pgpass; never place secret URLs in
  shell history or output. Use a consistent snapshot.
- Require `SELECT version_num FROM alembic_version` to return exactly
  `0012_provider_coverage` before this migration. Stop on divergence.
- Record counts and suitable checksums/snapshots of all existing business tables,
  especially `external_providers`, `provider_coverages`, `quotes`,
  `external_transactions`, `transaction_contexts`, `provider_events`, `trips`,
  `trip_items`, and `atlas_web_sessions`. Confirm actual names using the schema.
  Coordinate a controlled write window or account for legitimate concurrent
  traffic; unchanged counts are not meaningful if production writes continue.
- On the restored isolated DB, run upgrade 0012 → 0013, validate schema against
  models, insert one activation, downgrade to 0012, then upgrade to 0013 again.
  Confirm only `revenue_clicks` and its two indexes/check constraints were added;
  existing rows/checksums and foreign keys are unchanged. Downgrade destroys click
  events and belongs in this isolated validation, not routine production rollback.
- Only after those gates, execute the approved candidate's
  `alembic upgrade 0013_revenue_clicks` against `atlas_prod`, using the existing
  protected configuration. The migration adds no seed business data.
- Recheck revision, table/index/constraint definitions and invariants. **Do not
  rely on restarting the app to migrate:** `SessionStore` calls `create_schema`
  and auto-migrates existing DBs on startup. Migrate intentionally before restart.

### 3. Backend candidate and same-origin routing

- Before touching the real process, smoke the candidate backend with an isolated
  DB on a spare port. Test accepted and invalid JSON, rate limit and privacy of
  access/error logs through the candidate reverse proxy configuration.
- Advance production source only to the approved SHA with a fast-forward, using
  the inspected clean source layout. Restart only `atlas.service` when ready;
  capture old/new PID and require `/health` locally and publicly to return 200.
  Confirm private trip/session flows and native transaction invariants still work.
- Apply reviewed nginx routing and run `nginx -t` before reload. Never replace the
  entire live TLS configuration with the example HTTP server block.
- Verify `/revenue/click` POST reaches FastAPI and GET remains non-public (405).
  No CORS or public analytics read endpoint should appear. Keep the old frontend
  active until the backend and routing checks succeed.

### 4. Build and pre-switch frontend acceptance

- Build a new `/opt/atlas-frontend/releases/<timestamp>-<approved-sha>-revenue-r2b`
  from isolated approved source, never inside `/opt/atlas-frontend/current`.
- Supply only approved build-time revenue configuration from the protected
  `/etc/atlas/atlas-revenue-build.env` without echoing it. Ensure no test affiliate
  identifier enters production. Viator/DiscoverCars/Concierge remain unset until
  approved separately. Use the same build settings for client and SSR.
- Preserve older hashed assets if required for already-open browsers. Keep new
  robots/sitemap behavior authoritative; do not replace it with legacy assets.
- Start the new release on a spare loopback port and run
  `python3 scripts/smoke-revenue.py http://127.0.0.1:<port> --affiliate` from its
  source directory. Require all nine pages, canonical/meta/JSON-LD, disclosure,
  real HTTPS href, related links, 404/noindex, sitemap/robots. Confirm the partner
  URL exists in both bundles without printing config contents.
- Stop the candidate process. Only after all gates pass, replace the `current`
  symlink atomically and restart `atlas-frontend.service`. Record release/PID.

### 5. Post-switch acceptance and click probe

- Require active `atlas`, `atlas-frontend`, `nginx`, local frontend/backend health,
  public homepage and `/health`, and all nine public SSR routes at 200. Re-run the
  raw smoke locally and against `https://atlas.korsakovgroup.com --affiliate`.
  Unknown slug must be 404, sitemap XML and robots must be accessible at the
  public origin. Check that no public page launches private session requests.
- With an operator-approved test activation, POST one accepted payload using
  `utm_campaign=rollout_smoke`, `utm_source=direct`, `utm_content=link_001` and
  `route_slug=podgorica-kotor`. Record returned UUID; query **that UUID** to prove
  exactly one persisted row. Exclude the `rollout_smoke` campaign from reporting.
  Do not claim or create a booking; do not delete unrelated events or use demo
  reset to remove analytics. A manual probe is not partner conversion evidence.
- Open a real page on a real device, activate Kiwitaxi once, confirm ordinary
  outbound navigation and first-party event. Check the Kiwitaxi dashboard
  separately when available; Atlas cannot establish their referral attribution.
- Recheck database revision and existing business invariants. Backend PID should
  remain the new known healthy PID throughout the frontend switch, not restart
  unexpectedly. No quote, booking, provider or transaction state should change
  because of a click. Verify endpoint logs contain no payload, IP or raw query.

### 6. Rollback

- On a failed frontend/post-switch gate, atomically restore the recorded previous
  frontend symlink and restart only `atlas-frontend`; verify local/public health.
  Keep the compatible new backend and additive `0013` schema initially. Restore
  nginx routing if needed after `nginx -t`. Stop new traffic before investigation.
- **Do not simply restart the baseline backend against revision 0013.** Its
  Alembic directory ends at 0012 and startup auto-migration cannot resolve 0013.
  For a backend rollback, prepare/test a compatibility artifact with baseline
  business code plus the additive RevenueClick model and 0013 migration retained,
  or use a separately reviewed recovery procedure. This keeps existing click
  rows and avoids accidental downgrades. The recommended immediate rollback is
  frontend/proxy rollback with the new backward-compatible backend retained.
- Never automatically downgrade 0013 in production: it drops click telemetry.
  If schema rollback is unavoidable, stop telemetry writes, export/preserve events,
  take a fresh backup, obtain explicit operator approval for the destructive
  downgrade and validate the exact old code/revision combination in isolation.
- A full backup restore can overwrite legitimate writes after the backup; it
  requires a coordinated incident decision, not an automatic deployment script.
  Revalidate business rows, sessions, service PIDs and external transaction state.

## Aggregate reporting (private operator SQL only)

Do not expose raw events publicly. Example, with no invented conversion metrics:

```sql
SELECT provider, placement, route_slug, utm_source, utm_campaign, count(*) AS outbound_activations
FROM revenue_clicks
WHERE utm_campaign IS DISTINCT FROM 'rollout_smoke'
GROUP BY provider, placement, route_slug, utm_source, utm_campaign;
```

No automatic retention job or session-linked cleanup was introduced. Decide an
explicit retention policy separately; no dashboard or page-view tracking is live.

## Still NOT LIVE / outside this candidate's verification

Production migration, actual TLS nginx forwarding/reload, production SSH/service
changes, live click writes, partner-side Kiwitaxi referral reconciliation, Search
Console submission/indexing and approvals for other partners. Production rollout
requires the separate operator review above. No prices, inventory, confirmed
bookings or commissions have been asserted by this release.
