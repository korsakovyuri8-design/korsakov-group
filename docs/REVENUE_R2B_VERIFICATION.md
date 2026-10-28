# Revenue R2B — executed verification

Date: 2026-10-28 UTC. Development environment only.

- Repository: `korsakovyuri8-design/korsakov-group`.
- Base branch: `ccr-b496a55d-mr7ho5`.
- Base SHA: `a86d3131014c5d90a10316ad6b4dd95ea66d48af`.
- Working branch: `atlas/revenue-r2b-measurement-route-catalog`.
- Isolated worktree: `/workspace/atlas-r2b`; original checkout preserved.
- Remote base SHA matched the requested commit at preflight and was rechecked
  before delivery. No production write, deployment, migration or service command
  was performed. The pushed commit/PR identifiers are in the delivery message.

## Executed results

| Check | Result |
| --- | --- |
| Original frontend baseline | PASS — 28 files, 84 tests |
| Original Alembic head | `0012_provider_coverage` |
| Original migration suite | PASS — 10 tests |
| Final full backend suite | PASS — 1,283 passed, 2 PostgreSQL-only skips (covered below), 514.10s |
| New revenue API/privacy tests, SQLite | PASS — 69 tests |
| SQLite migration suite plus durable-session regression | PASS — 14 tests |
| PostgreSQL 16 revenue API + full migration suite | PASS — 80 tests |
| PostgreSQL-only queue/last-item concurrency regressions | PASS — 2 tests |
| Final full frontend suite | PASS — 31 files, 117 tests |
| TypeScript `tsc --noEmit` | PASS |
| Production client and SSR builds, no affiliate configured | PASS |
| Production client and SSR builds, dummy Kiwitaxi affiliate configured | PASS |
| Dummy affiliate URL in both client and server bundles | PASS |
| Raw SSR without configured affiliate | PASS — all 9 routes, no CTA |
| Raw SSR with configured affiliate | PASS — all 9 routes, real href and disclosure |
| h1/title/description/canonical/OG/Twitter/JSON-LD/related links | PASS — raw server HTML, all 9 routes |
| Original Podgorica–Kotor URL, no redirect | PASS |
| Unknown slug | PASS — actual HTTP 404 and noindex response header |
| Sitemap, HEAD, robots | PASS — correct types, absolute URLs, deterministic XML |
| Public root loader does not query private trip state | PASS — browser router test |
| Uvicorn real HTTP activation | PASS — 201, exactly one row, existing business rows unchanged |
| Uvicorn telemetry access-log suppression | PASS — real local HTTP request absent from log |
| Candidate nginx syntax | PASS — `nginx -t` in disposable nginx container |
| Whitespace/conflict check | PASS — `git diff --check` |
| Existing production read-only preflight | homepage 200, old transfer page 200 with SSR CTA/disclosure, health 200 |

The full SQLite run skips the two existing PostgreSQL-specific locking tests;
those were separately executed successfully on the disposable PostgreSQL instance.
An existing test double was extended with its now-required DB factory. The old
assertion that 0012 must be the latest migration was replaced by a coverage-ancestry
assertion; a new test pins the R2B head to 0013. Final results above are for the
corrected candidate, not for the earlier failing iterations.

The migration test upgrades a populated v1 fixture to 0012, snapshots every
existing table, upgrades to 0013, checks model/schema parity, inserts an event,
downgrades and repeats. Existing rows remain unchanged, and the new table has no
foreign keys. It finishes upgraded to 0013. Both SQLite and PostgreSQL executed
this test. The API integration test also proves a click survives actual demo reset.

UTM/HTTP tests cover all provider/placement combinations and all nine slugs,
unknown fields, identity-like values, oversized and chunked bodies, JSON-only
media types, duplicate keys, bad JSON, origin rejection, rate limiting and DB
errors. Browser-component tests cover beacon success/refusal/throw/absence,
keepalive fallback, rejected/throwing fetch and HTTP failure, unchanged navigation,
keyboard and middle-click, all three placements and no hover/focus/context-menu
counting. No real partner transaction is exercised.

## Test environment

- Python 3.12.14; pytest 9.2.0.
- FastAPI 0.162.0, Starlette 1.10.0, Pydantic 2.14.1.
- SQLAlchemy 2.1.6, Alembic 1.20.1, psycopg 3.3.6.
- Node 24.19.0, Bun 1.3.11; frontend dependencies installed from the unchanged
  `bun.lock` using `--frozen-lockfile`; Vitest 4.1.11.
- Dedicated disposable `postgres:16-alpine`, database `atlas_r2b_test`, loopback
  port 15432. No production database/configuration was loaded.
- PostgreSQL image digest:
  `sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab`.
- One non-failing backend warning: Starlette deprecates the current httpx TestClient
  adapter. No dependency files were changed to silence that warning.

Commands, behavior, accepted/rejected payload examples, attribution vocabulary,
privacy limits and the separate production rollout/rollback procedure are in
[REVENUE_R2B.md](REVENUE_R2B.md).

## Not executed / NOT LIVE

- Production DB backup/migration, live nginx TLS configuration reload, service
  restart, frontend release switch, real production click inserts.
- Production SSH/service-unit inspection; public preflight used HTTP reads only.
- Full backend suite against PostgreSQL: the relevant 80-test API/migration suite
  and the two database-concurrency tests ran there; the complete suite ran on SQLite.
- Real-device browser/partner referral reconciliation, Kiwitaxi dashboard proof,
  Search Console submission/indexing, partner booking/commission reporting.
- Viator, DiscoverCars and Concierge activation or approvals.
- Production deployment gates in the runbook. Development smoke results do not
  constitute `LOCAL_LIVE` or production acceptance.

## Exact changed files

The generated router tree was regenerated by Vite/TanStack; it was not hand edited.
`D` marks the old one-off route replaced by the dynamic route. `A` marks new files.

```text
M atlas-frontend/deploy/nginx-atlas.conf
M atlas-frontend/public/robots.txt
A atlas-frontend/scripts/smoke-revenue.py
A atlas-frontend/src/components/atlas/RevenueOutboundLink.tsx
M atlas-frontend/src/components/atlas/RevenuePanel.tsx
M atlas-frontend/src/components/atlas/TransportAffiliateCta.tsx
A atlas-frontend/src/lib/revenue/attribution.ts
A atlas-frontend/src/lib/revenue/attributionPolicy.json
A atlas-frontend/src/lib/revenue/transferRoutes.json
A atlas-frontend/src/lib/revenue/transferRoutes.ts
M atlas-frontend/src/routeTree.gen.ts
M atlas-frontend/src/routes/__root.tsx
A atlas-frontend/src/routes/transfer.$slug.tsx
D atlas-frontend/src/routes/transfer.podgorica-kotor.tsx
M atlas-frontend/src/server.ts
M atlas-frontend/src/test/app-routing.test.tsx
A atlas-frontend/src/test/revenue-tracking.test.tsx
M atlas-frontend/src/test/setup.ts
A atlas-frontend/src/test/sitemap-server.test.ts
A atlas-frontend/src/test/transfer-catalog.test.tsx
M atlas-frontend/src/test/transfer-podgorica-kotor.test.tsx
A docs/REVENUE_R2B.md
A docs/REVENUE_R2B_VERIFICATION.md
M hotelbot/app/atlas_web/app.py
A hotelbot/app/atlas_web/revenue.py
M hotelbot/app/db/models.py
A hotelbot/migrations/versions/0013_revenue_clicks.py
M hotelbot/tests/test_atlas_durable_sessions.py
M hotelbot/tests/test_migrations.py
M hotelbot/tests/test_provider_coverage.py
A hotelbot/tests/test_revenue_clicks.py
```
