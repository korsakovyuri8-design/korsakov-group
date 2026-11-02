# Atlas R2D.1 candidate — NOT DEPLOYED

Base commit `502a87eeb42b084b9d26bf7c67c90603b5fa68b0` (R2B production, PR #8 already merged). This patch is strictly a candidate, not authorization to migrate the running PostgreSQL instance or restart services.

## Favicon

Replace the old Lovable `public/favicon.ico` with the user's supplied Atlas A/gold-aircraft logo, include `apple-touch-icon.png` and `atlas-icon-192.png`; root head uses versioned icon URLs to bypass browser caches. Chromium tab favicon may need hard reload or a newly opened tab after deploying the *new built* frontend release.

## Pageview contract

- `POST /revenue/view` stores one event when a catalog landing page *hydrates* in a browser. No server-side rendering, search crawler, private trip state or unique visitor count is claimed.
- Stored fields: server UUID, catalog slug, reviewed UTM labels, server UTC timestamp. No IP, agent, referrer, URL/query, cookie, visitor/session identifier, name, trip or booking relation. No public GET/analytics endpoint.
- `201` only after a DB commit; `400/403/405/413/415/422/429/503` on the same categories as R2B clicks. No error response reveals submitted values. Write failure has no retry; direct browser navigation and the outbound CTA remain usable.
- Client effect invokes once per mounted route slug; a browser refresh or return navigation counts another view. Multiple tabs, lost beacons, JavaScript-disabled clients, middle clicks, ad-blockers and service degradation mean both numerator and denominator are approximate.
- Application cap 120 pageviews/minute per process; separate nginx global 120/min server bucket is required. This limits load but loses high traffic rather than falsely claiming full accounting.
- The old R2B click endpoint and schema remain unchanged. The new additive Alembic revision `0014_revenue_pageviews` adds one independent table and two indexes.
- `scripts/revenue_ctr.sql` reports daily observed *route_landing* Kiwitaxi click events / observed client-hydrated route pageviews. Excludes `utm_campaign=rollout_smoke`. Use `scripts/revenue_ctr_campaign.sql` to group by route, reviewed UTM source and campaign, without creating visitor identifiers. It is **event CTR**, not unique visitor conversion, partner booking rate or verified revenue; nullable when denominator is zero.

## Candidate test plan (isolated environments ONLY)

```
cd hotelbot
python -m pytest tests/test_revenue_pageviews.py tests/test_revenue_clicks.py tests/test_migrations.py -q
cd ../atlas-frontend
bun install --frozen-lockfile
bun run test
bunx tsc --noEmit
bun run build
```

Use disposable SQLite database or a **separately provisioned throwaway PostgreSQL test database**. Never set HOTELBOT_TEST_DATABASE_URL to production. Verify the existing 117+ frontend tests and migration roundtrips. The new view-related Vitest checks must pass, including the existing R2B click tests, which mock the new auto pageview effect.

## Production proposal — requires a separately verified rollout

1. Verify the exact candidate commit, source permissions, backup/restore of `atlas_prod` at revision 0013, current services and nginx TLS vhost. R2B and a real Android Chrome click must still pass.
2. On a restored isolated copy, rehearse migration `0013→0014`, writing/viewing one event as `atlas_app`, then downgrade/upgrade **only in isolated DB**, compare existing table content and constraints.
3. Stage an exact `/revenue/view` location in the **active TLS** vhost mirroring the security and header-stripping of `/revenue/click`, with `access_log off`, `error_log /dev/null`, no forwarded IP/UA/cookie/referrer. Define `limit_req_zone $server_name zone=atlas_revenue_views:1m rate=120r/m;` exactly once in the nginx `http` context. On production nginx the global limit zone is in `/etc/nginx/conf.d/`, not necessarily in the `sites-enabled` file. Test `nginx -t` before reload.
4. Build frontend candidate in an isolated release path. Ensure icon files are served; test all 9 SSR routes, robots, sitemap and the real affiliate URL. Frontend must stay on the R2B release until backend readiness.
5. Stop backend briefly. Fast-forward exact source commit (avoid `umask 077` for Git working-tree files; verify model/migration read rights for users `atlas`, `postgres`). Migrate PostgreSQL as `atlas_app` from a readable worktree. Restart R2D backend and verify health, `/revenue/click` and `/revenue/view` with real HTTPS POST and DB single-insert validation.
6. Atomically switch new frontend symlink; restart frontend; smoke all public routes plus Chrome pageview and outbound click under separate test UTM content labels. Do not redo the 0013 migration.
7. Roll back frontend via old symlink if necessary. **Do not blindly roll back backend to a code version whose Alembic head is below the active DB revision.** Do not downgrade production schema: it deletes pageview data.

Privacy review before public launch: confirm log exclusions at BOTH nginx and Uvicorn, rate-limits, and retention policy. No operational app should accidentally expose read-only analytics to the public.
