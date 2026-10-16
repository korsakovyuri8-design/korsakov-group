# Korsakov Atlas — Competition Release 1.0

## What it is

A single container serves the public demo. The page leads with **Paste a booking confirmation**, which offers **Import** and **Use sample bookings**. Below it is **Watch the 90-second demo**.

Every import runs the real pipeline: the deterministic parser, ingest, the Journey Graph, the timeline, the free windows, then the demo World and Discovery. The playback is one recorded run of that same pipeline.

| | |
|---|---|
| Code | `app/atlas_web/` (`app.py`, `intake.py`, `sessions.py`, `view.py`, `static/index.html`) |
| Image | `Dockerfile.atlas` |
| Deploy | `deploy/atlas_cloud_run.sh`, `deploy/cloudbuild.atlas.yaml` |
| Gates | `tests/test_atlas_web.py`, `tests/test_atlas_demo.py` |

## Endpoints

| | |
|---|---|
| `GET /` | the page (import first, the 90-second demo second) |
| `GET /demo/sample` | a new session holding the three sample bookings |
| `POST /demo/import` | `{session?, text}` or `{session?, sample}` → what Atlas understood, plus the trip |
| `GET /demo/trip/{session}` | the current trip picture |
| `POST /demo/reset` | `{session}` → the session and everything in it are gone |
| `GET /health` | liveness |

`sample` takes one of these values:
- `flight`, `hotel`, `restaurant`;
- `transfer` (the "Arrange transfer" step);
- `flight_change` (the disruption step).

The trip offers only the next step that makes sense, under **Try next**, labelled *Demo*.

## What a visitor sees

1. **The import card.** "Atlas understood this booking": the provider, the booking, the route, the date and the status. For an update it says what changed, for example "Departure moved from 09:15 to 11:15." A document Atlas already has is recognised as such.
2. **An unknown format.** "Atlas can't read this booking format yet. No trip data was changed." Nothing is stored.
3. **The trip:**
   - the route and dates, and the bookings;
   - **Needs attention**: missing transport, a connection that may no longer work, overlapping bookings;
   - activities that fit the first free window the demo World covers;
   - the plan for each day.

The wording follows the rule that planned location is not observed location: "By your plan you are due at …".

## Privacy commitments (each one enforced in code and pinned by a test)

| Commitment | Where |
|---|---|
| Raw documents are never logged | Logs carry only the outcome and the kind. The 500 handler logs the exception type, never the body. The container runs with `--no-access-log`. Test: `test_raw_documents_are_never_logged_or_echoed`. |
| Raw documents are never returned | Responses carry parsed facts only. Same test. |
| Nothing is kept after the session | Each session is its own in-memory SQLite database. It ends on reset, after a 45-minute TTL, or when 200 sessions are open (the least recently used goes first). There is no disk and no shared database. Tests: `test_reset_deletes_the_session`, `test_sessions_expire`. |
| Nothing is used for training, and nothing goes to an LLM | `app/atlas_web` imports no LLM client and no HTTP client. Unknown formats are refused, not "tried with a model". Test: `test_the_service_never_imports_an_llm_or_an_http_client`. |
| One document is at most 150 KB | `intake.MAX_DOCUMENT_BYTES`; the response is 413 with a plain message. Test: `test_a_document_over_the_size_limit_is_refused`. |
| Rate limits per visitor | 10 new sessions, 30 imports and 120 reads per minute; the response is 429. The key is the last `X-Forwarded-For` hop, which the visitor cannot forge. Test: `test_requests_are_rate_limited_per_visitor`. |
| HTML is reduced to safe text | Tags, scripts, styles, images and links are dropped before parsing. The page escapes every value it shows. Test: `test_html_email_is_reduced_to_text_before_parsing`. |
| No outbound requests from document content | Nothing in a document is ever fetched. CSP on the page: `default-src 'self'`, a script nonce, `img-src 'self' data:`, `connect-src 'self'`; only Google Fonts may load from outside. Test: `test_the_page_forbids_outbound_loads_beyond_fonts`. |
| The session token holds no personal data | It is 24 random bytes (`secrets.token_urlsafe`), kept only in the tab (`sessionStorage`). Test: `test_the_session_token_is_random_and_holds_no_personal_data`. |

## Run it locally

```
uvicorn app.atlas_web.app:create_app --factory --port 8080
# or the production image:
docker build -f Dockerfile.atlas -t atlas-demo . && docker run -p 8080:8080 atlas-demo
```

## Deploy to Cloud Run

```
gcloud auth login
PROJECT=<project-id> deploy/atlas_cloud_run.sh                  # region europe-west1 by default
PROJECT=<project-id> MIN_INSTANCES=1 deploy/atlas_cloud_run.sh  # demo day: no cold start
```

The script runs these steps:
1. It enables Cloud Run, Cloud Build and Artifact Registry.
2. It builds `Dockerfile.atlas` in Cloud Build.
3. It deploys an unauthenticated service with **max 1 instance and session affinity**. Sessions live in memory, so a second instance would not see a visitor's trip.
4. It prints the `*.run.app` URL and checks `/health`.

With `MIN_INSTANCES=0`, an idle service scales to zero. That also ends every session, which is acceptable for a demo. Use `1` for the presentation.

## Custom domain: atlas.korsakovgroup.com

The apex `korsakovgroup.com` is served by GitHub Pages (`CNAME` at the repository root). A subdomain record does not touch it.

1. Verify the domain for the Google account once: `gcloud domains verify korsakovgroup.com` (Search Console, a TXT record).
2. Create the mapping:
   ```
   gcloud beta run domain-mappings create --service atlas-demo \
     --domain atlas.korsakovgroup.com --region europe-west1
   ```
3. At the DNS provider for korsakovgroup.com, add `atlas  CNAME  ghs.googlehosted.com.`
4. Google issues the HTTPS certificate on its own; that usually takes 15–60 minutes.

Until DNS is ready, the `*.run.app` URL is the public demo.

## Release check (incognito)

1. Open the URL and press **Use sample bookings**. Expect London → Podgorica → Kotor, 18–21 October, and **Missing transport**.
2. Press **Arrange transfer**. Expect no issues, free time 16:30–19:30, and "You have 3 hours before dinner. Here are 3 things that actually fit."
3. Press **Simulate a flight change**. Expect "Departure moved from 09:15 to 11:15." and **Transfer needs rebooking**.
4. Press **Start over**, use the *Demo Air booking confirmation* chip (or paste that e-mail), and press **Import**. Expect "Demo Air · Flight DM1432, London → Podgorica, 18 Oct, Confirmed".
5. Paste any other text. Expect "Atlas can't read this booking format yet."
6. Use **Watch the 90-second demo**, with **Show narration** for the pitch (or open `/#narration`).

## Known limits of 1.0 (deliberate)

- **Formats.** Only Atlas's deterministic formats are read: the Demo Air, hotel, DemoTable and Demo Transfers e-mails. Real airline and hotel formats, Gmail and booking APIs are out of scope for this release.
- **Instances.** One instance. Sessions do not survive a restart, a redeploy or scale-to-zero.
- **The demo World.** It is synthetic and covers Kotor Old Town only. Free windows elsewhere are computed, but no activities are suggested.
- **Fonts.** They load from Google Fonts. Without them the page falls back to system faces.
