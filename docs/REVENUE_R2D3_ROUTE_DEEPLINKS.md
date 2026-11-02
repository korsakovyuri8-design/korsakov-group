# Atlas R2D.3 — Kiwitaxi route-specific partner links (candidate, NOT deployed)

Base: `88b7774cc330ebc35f406a15272ca436b7b0eacc`, Atlas R2D.1.
Frontend-only. No backend/schema changes. Two links from user's Kiwitaxi affiliate
panel are attached to two exact public catalog routes. No URLs or partner IDs are
hardcoded in Git. Each is injected from the approved **build-time** environment.

| Atlas public slug | Affiliate build variable | Required partner channel |
| --- | --- | --- |
| `podgorica-kotor` | `VITE_ATLAS_AFFILIATE_KIWITAXI_MNE_URL` | `atlas_mne` |
| `tashkent-airport-samarkand` | `VITE_ATLAS_AFFILIATE_KIWITAXI_UZ_URL` | `atlas_uz` |

- The **existing** `VITE_ATLAS_AFFILIATE_KIWITAXI_URL` remains mandatory for an
  affiliate CTA and is the fallback for the other seven catalog routes, the
  revenue panel and the missing-transport CTA.
- A route override must have `https://kiwitaxi.com` (no subdomain, port,
  password or fragment), the exact configured route path, exactly `pap` and
  `chan` search keys, the expected channel, and a nonempty partner ID.
  Malformed or missing overrides fall back to the approved general link.
- No attempt is made to construct a deep link by combining arbitrary cities,
  user-provided private-trip text or query-string data.
- The links do not prove booking availability or commission; keep existing
  R2D.1 `revenue/click` and `revenue/view` tracking unchanged.

## Manual partner validation BEFORE production

1. Open each exact **generated** link in Chrome. Confirm it loads the intended
   route, not a 404 or a different direction. Avoid booking or paying.
2. Check in the partner panel that links have the correct account and channel.
   The affiliate generator may output a URL even if the target route is not
   actually available; do not release invalid links.
3. Keep the full generated URLs private to the approved production build env
   `/etc/atlas/atlas-revenue-build.env`; protect mode 600. Never send URLs
   to public code-review logs, and never use shell `echo` to print them.
4. Add BOTH build variables listed above only when they are manually verified.
   The build must use the existing approved real generic affiliate variable
   plus the two specific route variables; all three are embedded in public
   frontend URLs by design (affiliate links are public when displayed).
5. Run `bun run test`, `bunx tsc --noEmit` and `bun run build` on an isolated
   checkout, then inspect all nine SSR affiliate URLs with an exact allowlist.
6. Create a draft PR and stage an immutable frontend release. No backend
   restart, nginx edit or Alembic migration is necessary.

**Rollback:** Restore only the previous frontend release symlink, restart
`atlas-frontend.service` and validate the nine SSR routes. Do not downgrade DB.
