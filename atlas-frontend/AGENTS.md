<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Atlas frontend rules
- View models live in `src/lib/atlas/domain.ts`; providers (`providers/demo.ts`, `providers/api.ts`) implement one `AtlasProvider` interface, selected only by env in `src/lib/atlas/api.ts` (production builds default to api, dev server to demo; `VITE_ATLAS_PROVIDER=api|demo` overrides; base URL `VITE_ATLAS_API_BASE`, default same-origin) — so no component knows which source is active.
- ApiAtlasProvider only renames fields from the existing `/demo/*` API; server responses replace UI state (never mutated locally); session token lives only in sessionStorage — backend owns journey logic and CORS stays closed.
- Demo state switch exists only when the provider exposes scenarios (design/QA); the live provider exposes none.
- Components read data via `useAtlas()` (state, context, trip); never import `demo-data.ts` except `/demo` story — keeps the source swappable.
- Trip state (ON_PLAN, FLIGHT_CHANGED, …), statuses, windows and "now" come from the provider; `summary.ts` only formats — backend owns business logic.
- Fixture values must match backend fixtures exactly; missing fields stay absent — never made more realistic.
- Screens use `<AtlasMap>`; swap the `activeAdapter` (e.g. MapLibre using `coords`) without touching screens.
- The UI is frozen until the backend ships the pending contract fields (current time/next steps, richer booking item views, affected/on-hold free windows); every such field is added to `domain.ts` as optional and mapped only when actually received — never defaulted, inferred or pre-built for.
