# Iteration 6 - real-data pilot: Kotor + Budva

**Purpose:** expose the frozen World Fabric (Iteration 5.1 ER rules), the Discovery Engine and the resolver to messy real data. The deliverable is knowledge about reality, not metrics. No new product features. No ER, threshold or policy change before the raw result and the first human labels exist.

## Status

**Blocked on network access.** The tooling is built and tested offline; the first real ingest has not run. The cloud environment's network policy denies every source host:

- `overpass-api.de`
- `api.openstreetmap.org`
- `www.openstreetmap.org`
- `download.geofabrik.de`
- `query.wikidata.org`
- `www.wikidata.org`
- `download.geonames.org`
- `www.montenegro.travel`

The proxy answers 403 to CONNECT, and the web-fetch tool is behind the same egress policy. Package registries are reachable.

**To unblock, either:**

- **A.** Allow only `overpass-api.de` and `query.wikidata.org` under Network access → Allowed domains (https://code.claude.com/docs/en/cloud-environments#network-access), then run `python tools/pilot_fetch.py`.
- **B.** Fetch elsewhere and import the **original HTTP response bodies**, not a cleaned CSV:
  1. `python tools/pilot_fetch.py --print-queries` prints the four exact queries (2 sources × 2 boxes) and their endpoints. Overpass takes a POST with `data=<query>`; Wikidata takes a GET with `format=json&query=<query>`.
  2. Save each response body as-is and import it:

     ```
     python tools/pilot_fetch.py --import osm kotor=osm_kotor.json budva=osm_budva.json --fetched-at 2026-10-08T10:00:00+00:00
     python tools/pilot_fetch.py --import wikidata kotor=wd_kotor.json budva=wd_budva.json --fetched-at 2026-10-08T10:05:00+00:00
     ```

     The import keeps the bodies byte for byte (sha256 in `raw/SHA256SUMS`) and marks the dump `fetched_outside_environment`.

Everything after the fetch runs offline from the dumps.

## Sources

| | OpenStreetMap | Wikidata |
|---|---|---|
| Type | crowd-sourced geospatial database | crowd-sourced structured knowledge base |
| Access | Overpass API (`overpass-api.de`), one query per scope box | SPARQL query service (`query.wikidata.org`), one query per scope box |
| Coverage expected | restaurants, cafés, bars, pharmacies, ATMs, banks, supermarkets, parking, beaches, viewpoints, museums, places of worship, monuments… | landmarks and culture: museums, churches, monasteries, fortifications, palaces, monuments, beaches, parks, theatres. Few businesses |
| Fields | name, `name:<lang>`, `alt_name`, `int_name`, coordinates (ways and relations: centre), `addr:*`, phone / contact:phone, website, `opening_hours` (OSM syntax), `check_date:opening_hours`, cuisine, wheelchair…, element edit `timestamp` | labels in sr / sr-el / sh / hr / en / de / ru / it, coordinates, P31 types, website (P856), phone (P1329), address (P6375), OSM node / way / relation ids (P11693 / P10689 / P402) |
| Identity fields | OSM element id; `wikidata=Q…` tag; phone; website; street address | QID; OSM element ids; website; phone; address |
| Licence | **ODbL 1.0** ([OSMF licence FAQ](https://osmfoundation.org/wiki/Licence_and_Legal_FAQ), [legal structure](https://wiki.openstreetmap.org/wiki/License/Legal_Structure)) | **CC0 1.0** for structured data ([Wikidata:Licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)) |
| Attribution | required: "© OpenStreetMap contributors, ODbL", wherever a substantial amount of the data is shown | none required |
| Redistribution | allowed; **share-alike for a publicly distributed derived database** (our canonical world, if it mixes OSM data and is published) | unrestricted |
| Update mechanism | re-query (or minutely diffs; not used in the pilot) | re-query |
| Expected freshness | varies per element; the element `timestamp` is the last edit, not a field check. `check_date:*` tags are checks | varies per item; there is no observation date per statement, so the fetch time is used |
| Source class (frozen policies) | `directory` | `directory` |

The two sources are structurally different. They are linked by *explicit* identifiers in both directions: an OSM `wikidata=Q…` tag, and Wikidata's OSM node / way / relation properties. That makes their reconciliation meaningful, and it measures how often real sources give positive identity evidence.

**Not used until their terms are verified:**

- **The Central Tourist Register of Montenegro** ([gov.me](https://gov.me/en/article/central-tourist-register)). It is the official register of tourism and catering businesses and would be the natural third, authoritative source. Its reuse licence is not confirmed, and the national open-data portal (data.gov.me, relaunched December 2024) could not be checked from here.
- **TO Kotor / TO Budva event calendars.** No data licence was found, and scraping without permission is out. So there is **no legal event source yet**, and the event pilot (step 13) is not forced.

**No architecture decision on mixing and redistribution is taken here.** ODbL is not just an attribution line. If the canonical world is ever distributed as a database or as a substantial extract, the derivative vs collective database question and share-alike need their own analysis. The World Fabric keeps per-source provenance, licence and redistribution terms on every assertion (`world_sources`, `_attribution`). A discovery service can therefore be designed to respect each source's limits rather than "mix everything and hand the result out".

**Licence consequence to keep in mind.** OSM's share-alike applies to a *publicly distributed* derived database. Internal use and Produced Works (answers shown to a traveller, with attribution) are fine. Publishing the merged canonical world as a database would oblige us to share it under ODbL.

## Scope

The bounding boxes are in `data/pilot/kotor_budva/pilot.yaml`:

- **Kotor:** old town, Dobrota, Škaljari, Muo.
- **Budva:** Budva, Bečići, Rafailovići, Sveti Stefan.

The categories are those the product already supports. The category maps are data (OSM `key=value` and Wikidata P31 → existing subcategories). An unmapped tag becomes `unclassified` plus an issue. Examples are `tourism=attraction` and `shop=convenience`, and Wikidata types outside the list. Those counts are reported, not hidden. No category architecture was added.

## Methodology locked BEFORE any real data (no retroactive changes)

1. **An OSM↔Wikidata explicit-id link is not independent confirmation.** The OSM `wikidata=Q…` tag and Wikidata's OSM-id properties are often maintained by the same editors, from the same knowledge: one data chain, not two witnesses. ER still uses the link as it is frozen, but every MATCH reports the link's direction (`osm_wikidata_tag_only`, `wikidata_osm_property_only`, `both_directions`) and its **independent support** (phone / domain / address that SUPPORTS) separately. The report counts `only_by_id_link_chain` separately from `with_independent_support`.
2. **Same-source look-alikes stay a separate report** (`same_source_lookalikes.csv`). ER is cross-source by design, so internal OSM duplicates never reach the main identity metrics. They are counted and labelled apart.
3. **Every audited pair records why it was a candidate** (`candidate_reason`: `identifier:<kind>`, `geo:300m`, `time:*`) next to the decision rule. A bad pair can then be attributed to blocking or to the decision rule.
4. **UNSURE is its own category.** It is neither an algorithm error nor a success: it is excluded from precision, false merges and false splits, and counted apart (`unsure_by_group`).
5. **The first raw report is frozen** before any change to category maps, the normalizer, ER or policies. Even a taxonomy bug goes into the report first.
   - Reports are write-once (`out/<run>/`; `--run raw_v1` is the first). Each carries its provenance: the dump sha256s, the `pilot.yaml` and `policies.yaml` sha256s, the code commit and a dirty flag.
   - Raw dumps are write-once too. `raw/SHA256SUMS` records each response body's hash and the dump file's hash.

6. **The human-audit sample is pre-registered and reproducible.** It is defined in `tools/pilot_run.py` (`SELECTION_ALGORITHM`), and `out/<run>/audit_sample.json` records `audit_sample_version`, the selection algorithm text, `random_seed`, `source_dump_hashes`, `code_commit` and per-stratum population and sample sizes.
   - **Order.** Pairs are put in a deterministic order: by record, then by a stable key of the candidate (its smallest member record).
   - **MATCH strata:**
     - explicit id only;
     - explicit id + independent support;
     - contact-based;
     - address-based;
     - other rule.
   - **AMBIGUOUS strata:**
     - multiple candidates;
     - support + contradiction;
     - name + geo only;
     - other.
   - **Difficult NO_MATCH** is a machine criterion fixed now: name similarity ≥ 0.6, or ≤ 50 m, or any SUPPORTS or CONTRADICTS signal.
   - **Sample size.** Each stratum gets min(size, max(10, ⌈target × share⌉)). The manifest records `target_allocation` (50 / 50 / 30), `minimum_per_nonempty_stratum` (10) and `actual_sample_size`. The target is **not a cap**: the per-stratum minimum can raise the actual sample above it. Easy explicit-id matches therefore cannot crowd out contact or fuzzy merges.
   - **Census.** Unusual merges and every pair with a contradicting signal are audited in full.
   - **Scoring.** `pilot_labels.py` reports, for every stratum, `population_size`, `n_sampled`, `n_labeled`, `n_same`, `n_different` and `n_unsure`, plus precision per MATCH stratum. The overall precision is weighted by **population** sizes, not by labelled rows. Census and difficult groups find errors; they do not estimate rates. Confidence intervals are deferred and do not hold up the pilot.
   - **Reproducibility check.** Two independent runs on the same dumps and code produce the same sample (tested).
7. **`fetched_at` is the moment of the actual HTTP request**, not of the import. It must carry a UTC offset, lie in the past, and not precede the data's own timestamp (Overpass `timestamp_osm_base`). Otherwise the import is refused.

8. **Blind labelling (hard rule): human ground-truth labelling must not expose the algorithm's predicted class.**
   - **The view.** Labels are made in `out/<run>/audit_blind.csv`, generated from the same frozen sample. It shows only source values: source, name, other names, address, coordinates, distance, phone, website, external ids, category and the source URL for each side, plus empty `human_label` and `human_note`. It carries **no** decision, stratum, rule, candidate reason or evidence state. Pair ids are opaque hashes, and rows are ordered by them, so neither the id nor the row order reveals the sample group.
   - **What is refused.** The technical audit (`audit_pairs.csv` / `.jsonl`) is for diagnosis after labelling, and `pilot_labels.py` refuses it as a label source.
   - **Labels freeze.** The labels file's sha256 is recorded with the result. A result from one labels file is never overwritten by another; a new `label_metrics_<hash8>.json` is written instead. Only then are the labels joined to the algorithm's output by pair id.

9. **Labelling procedure (procedural rule, no code).** The primary label is set **only from the content of `audit_blind.csv`**, which comes from the frozen dumps.
   - **Do not open the external links before the first label.** The `url_*` columns are there for a later stage. Opening them first would quietly add live external information, which the frozen evidence does not contain, to the ground truth.
   - **The labels mean:**
     - `SAME_ENTITY` / `DIFFERENT_ENTITY`: provable from the frozen audit evidence;
     - `UNSURE`: the frozen evidence is not enough to decide. A high UNSURE rate is itself a product result: the sources do not carry enough identity evidence.
   - **External verification**, if needed, is a **separate, later stage** and a separate source of evidence. It goes into its own file (`labels_<run>_external.csv`: `pair_id, label, externally_verified=yes, evidence_url, checked_at, notes`). It never edits the primary labels, and it is reported apart from them.

10. **External fetch protocol (procedural, fixed before data).**
    1. **Checkout.** Use **9e802c4**, the pre-data code checkpoint. c19d0bd and later commits change documentation only and are equally safe, but 9e802c4 is the reference.
    2. **Check the queries before any network access.** Generate the queries from the frozen `pilot.yaml` with `pilot_fetch.query_for`, never by copying from a chat. Check their SHA256 values; **on any mismatch, no fetch takes place**.

       | query | sha256 (verified at 9e802c4) |
       |---|---|
       | osm / kotor | `72c74ecc4490e5030e61d747cde2a9f57dcd2b1f87e5a69c92058159f6912a01` |
       | osm / budva | `0acccd58574f6242b635bd1c35f05ba6f194364a55cccef9b1b64c33c121493d` |
       | wikidata / kotor | `b5fa9c15ad7837fbb70680c39a4398ea5b4e4410abe0877190d7ac4192322665` |
       | wikidata / budva | `10da98b26c2a0062a2506d312da34fbe4cda98c8ec14d1fe108a395a2346d3f9` |

    3. **Preferred path:** `python tools/pilot_fetch.py`. It writes the two source-level dumps and `raw/SHA256SUMS` in the format the pilot expects.
    4. **Manual path (curl):**
       - every response body is saved with no transformation at all;
       - the real time of each of the four HTTP requests goes to `fetch_times.txt`, together with the method if Overpass was not queried by POST;
       - on `--import`, the source-level `fetched_at` is the **later** of that source's two request times, matching the frozen implementation, which records one time per source.
    5. **No viewing or editing of the JSON between fetch and freeze.** The order is fetch → raw bytes → SHA256 → `raw_v1`. Even obvious junk in the data goes into the first raw result.
    6. **No re-fetch because a result looks small or odd.** A valid HTTP response to a query with the frozen hash *is* the experiment's data. A repeated fetch is a different time sample and counts as a **separate run** (`--run raw_v2`, own dumps); it never "corrects" the first one. A non-JSON or error response (rate limit, timeout, HTML error page) is not data: it is kept for the record and the fetch is reported as failed, not imported.

11. **Acquisition-protocol amendment A1 (approved by Yuri; recorded here BEFORE any post-amendment HTTP request).** Three unchanged attempts under the original all-or-nothing source fetch each failed on a transient Overpass `504 Gateway Timeout`:

    | Run | Time (UTC) | Outcome |
    |---|---|---|
    | [37662574988](https://github.com/korsakovyuri8-design/korsakov-group/actions/runs/37662574988) | 17:53 | osm / kotor 504 |
    | [37663247953](https://github.com/korsakovyuri8-design/korsakov-group/actions/runs/37663247953) | 17:58 | osm / kotor 200, osm / budva 504 (`Dispatcher_Client::request_read_and_idx::timeout`) |
    | [37690001192](https://github.com/korsakovyuri8-design/korsakov-group/actions/runs/37690001192) | 21:31 | osm / kotor 504 (a single request, sent 21:31:28.35, answered 21:31:36.80, 695-byte body) |

    These three runs belong to the **old** protocol. They are kept as historical failed attempts and are **excluded from `raw_v1`**. **The HTTP 200 osm / kotor body from run 2 is not reused**: every observation used by `raw_v1` must be obtained after this amendment is committed. That rules out any selection among bodies seen before the rule existed.

    **What changes: only the atomicity of acquisition.** Each `{source, box}` is an independent, immutable HTTP observation:
    - `osm / kotor`
    - `osm / budva`
    - `wikidata / kotor`
    - `wikidata / budva`

    A failed observation does not invalidate the successful observations of the same campaign. Only missing or failed observations may be attempted again, and only by an explicit later request for those observations alone. A successful observation is frozen and is **never fetched again** for `raw_v1`.

    **What does not change.** Queries, query hashes, endpoints, HTTP methods, category maps, normalization, ER, field policies, sampling, labelling, Discovery and transaction / product code. The frozen `pilot_fetch.py` is not modified; its network path is not used for A1, and its frozen `--import` path assembles the result.

    **Per observation:**
    1. Check out pilot commit `9e802c4dd8fa9a6ecabbd4279f936f8959970daf`.
    2. Generate the query from the frozen `pilot.yaml` with `query_for`, and verify its pre-registered SHA256 (table in §10) **before** any network access.
    3. Make **exactly one** HTTP request with the frozen endpoint, method and headers (the `User-Agent` and `Accept` values come from the frozen `pilot_fetch.py`). There are no automatic retries.
       - OSM: `POST https://overpass-api.de/api/interpreter`, form field `data=<exact query>`.
       - Wikidata: `GET https://query.wikidata.org/sparql` with `query=<exact query>`, `format=json`, `Accept: application/sparql-results+json`.
    4. Save the response bytes byte for byte **before** any parsing.
    5. Record: source, box, query SHA256, endpoint, method, `request_sent_at`, `response_received_at`, HTTP status, content type, byte count, body SHA256, workflow run id and workflow commit.
    6. **Success = HTTP 2xx and valid JSON.** Anything else (a non-2xx status or invalid JSON) is kept as failure evidence and is **not** data.

    For Overpass, the presence of a top-level `remark` field (Overpass's way of reporting a runtime problem inside an HTTP 200) is recorded as metadata only. It is **not** part of the approved success criterion.

    **Execution.** An acquisition-only GitHub workflow (`.github/workflows/pilot-acquire.yml`):
    - one job per observation, `fail-fast: false`, `max-parallel: 1`, so the two Overpass calls are never simultaneous;
    - a failing `osm / kotor` does not stop `osm / budva` or the Wikidata observations;
    - each observation uploads its own artifact;
    - on a failure there are no automatic retries; the result is reported, and a later run may request only the failed observations.

    **Assembly** happens once four post-amendment successful observations exist:
    - the four untouched bodies go through the frozen `pilot_fetch.py --import`;
    - for each source's single `fetched_at`, the later of its two actual request times is used, as already pre-registered for imports;
    - both per-box times, the artifact ids and the run ids are kept in an acquisition manifest (`fetch_times.txt`);
    - SHA256 as usual.

    The step stops at assembled raw dumps + `SHA256SUMS`, with no `pilot_run`.

12. **Acquisition-protocol amendment A2 (approved by Yuri; recorded here BEFORE any post-A2 HTTP request).** The first A1 campaign ([run 37745652338](https://github.com/korsakovyuri8-design/korsakov-group/actions/runs/37745652338), 2026-10-08 07:47–07:48 UTC) produced **no** successful observation. It is kept as failure evidence, and nothing from it enters `raw_v1`.

    | Observation | Result |
    |---|---|
    | osm / kotor | 504, 695 B |
    | osm / budva | 504, 695 B |
    | wikidata / kotor | 403, 141 B `text/plain` |
    | wikidata / budva | 403, 141 B `text/plain` |

    The Wikidata 403 body (artifact 11535771907, checked by Yuri) cites the Wikimedia robot / User-Agent policy. That policy requires automated clients to send an identifiable `User-Agent` with a real contact (a URL, email or wiki user). The frozen UA's `contact: repository owner` gives no actionable contact.

    **The only change in A2 is the `User-Agent` header of future observations.**

    | | User-Agent |
    |---|---|
    | frozen (`pilot_fetch.USER_AGENT`) | `hotelbot-world-pilot/0.1 (real-data pilot, Kotor/Budva; contact: repository owner)` |
    | A2 | `hotelbot-world-pilot/0.1 (https://github.com/korsakovyuri8-design/korsakov-group; real-data pilot, Kotor/Budva) python-httpx/0.28.1` |

    The contact is the public GitHub repository URL, following Wikimedia's recommended `name/version (contact) library/version` form. No email is used.

    **Unchanged:** query text, query hashes, endpoints, HTTP methods, SPARQL, boxes, `Accept`, timeout, category maps, ER, normalization, sampling, labelling. The frozen `pilot_fetch.py` itself is not modified; the workflow sends the A2 string and records it in each `observation.json`. Everything else in A1 still applies: one request per observation, bytes before parsing, success = 2xx + valid JSON, no automatic retries, and a success is never refetched.

    **Execution of A2:**
    - Only `wikidata / kotor` and `wikidata / budva` are attempted now. Overpass is not re-queried to test a header.
    - If both return 2xx + valid JSON, they are frozen, and acquisition stops there.
    - If either returns 403 again, acquisition stops and the result is reported. No alternative headers, authentication, endpoints or repeated requests without a new decision; the next step would be another machine or another official access route.
    - The two OSM observations stay pending under A1 for a separate later attempt, with the same endpoint.

13. **Acquisition ledger for `raw_v1` (observations frozen under A1 + A2).** A successful observation is never fetched again.

    | Observation | Status | Run / artifact | Sent → received (UTC) | HTTP | Bytes | Body SHA256 |
    |---|---|---|---|---|---|---|
    | wikidata / kotor | **frozen** | [37751323718](https://github.com/korsakovyuri8-design/korsakov-group/actions/runs/37751323718) / 11537783193 | 2026-10-08 08:40:03.950155 → 08:40:04.647614 | 200 `application/sparql-results+json` | 22,904 | `68ea566c548b4829b9f98f4829ad081373809f45a0eeb0207a782f509b571947` |
    | wikidata / budva | **frozen** | [37751323718](https://github.com/korsakovyuri8-design/korsakov-group/actions/runs/37751323718) / 11538206827 | 2026-10-08 08:40:15.201942 → 08:40:15.500087 | 200 `application/sparql-results+json` | 16,522 | `3c15df8cfe34231dc2f5cab084c81d97232cbb9713266f982555fee34a7653c7` |
    | osm / kotor | pending | - | - | - | - | - |
    | osm / budva | pending | - | - | - | - | - |

    **Failed OSM attempts under A3** (`overpass.private.coffee`, [run 37754244471](https://github.com/korsakovyuri8-design/korsakov-group/actions/runs/37754244471), workflow `e6cff47`; failure evidence, not data):

    | Observation | Sent → received (UTC) | HTTP | Body | Artifact |
    |---|---|---|---|---|
    | osm / kotor | 2026-10-08 09:05:47.234097 → 09:05:48.192692 | 500 `text/html; charset=iso-8859-1` | 646 B, `37b2ff87b09dfe5a169c16650a4d67451afa5e9b734b27c42379bfbdb024e542` | 11539721294 |
    | osm / budva | 2026-10-08 09:05:57.480669 → 09:06:00.932283 | 500 `text/html; charset=iso-8859-1` | 646 B, same SHA256 | 11538769302 |

    Per A3, automatic activity stopped and the endpoint is not changed again without a new decision.

    The two Wikidata observations were made with workflow commit `64c828f` and pilot commit `9e802c4`, with the A2 User-Agent and the pre-registered query hashes. The bodies were not inspected beyond the HTTP status, content type, size, hash and the JSON-validity check.

14. **Acquisition-protocol amendment A3 (approved by Yuri; recorded here BEFORE any post-A3 HTTP request).** The pre-registered Overpass endpoint `https://overpass-api.de/api/interpreter` returned 504 on every OSM observation at several times of day:
    - runs 1–3 (old protocol), 2026-10-07 17:53, 17:58 and 21:31 UTC;
    - the A1 campaign, 2026-10-08 07:47–07:48 UTC: `osm/kotor` and `osm/budva`.

    This is an acquisition-availability problem, not a defect of the query or the data processing. All those observations remain failure evidence and are excluded from the dataset.

    **The only change in A3 is the endpoint of future OSM observations:**

    | | Overpass endpoint |
    |---|---|
    | frozen (`pilot_fetch.ENDPOINTS`) | `https://overpass-api.de/api/interpreter` |
    | A3 | `https://overpass.private.coffee/api/interpreter` |

    The A3 server is the Private.coffee Overpass instance, formerly kumi.systems, listed in the OpenStreetMap wiki's public Overpass instances as a global public instance. It executes the same Overpass QL over the same OSM data. The endpoint actually used is recorded in every `observation.json`.

    **Unchanged:** OSM query text and SHA256, boxes, `POST` with form field `data`, the A2 User-Agent, timeout, categories, normalization, ER, field policies, audit methodology, sampling, labelling and observation atomicity (A1). The Wikidata endpoint is unchanged.

    **Frozen observations stay frozen:** `wikidata/kotor` (artifact 11537783193) and `wikidata/budva` (artifact 11538206827) are never refetched.

    **Execution of A3.** Only `osm/kotor` and `osm/budva` are run, sequentially, one request each, with no automatic retries.
    - A success is frozen immediately and never requested again.
    - A failure is kept as evidence; automatic activity stops, and the endpoint does not change again without a new decision.
    - Stop at the OSM artifacts: no assembly and no `pilot_run` without a new instruction.

15. **Acquisition-protocol amendment A4 (approved by Yuri; recorded here BEFORE any post-A4 HTTP request).** A3 failed. `overpass.private.coffee` returned the same generic Apache HTTP 500 page for both boxes (run 37754244471). The preserved bodies, read by Yuri, show a server-side Internal Server Error (`Apache/2.4.66 (Debian)`, `webmaster@localhost`). A3 is not retried.

    **The only change in A4 is the endpoint of future OSM observations:**

    | | Overpass endpoint |
    |---|---|
    | frozen | `https://overpass-api.de/api/interpreter` |
    | A3 (failed) | `https://overpass.private.coffee/api/interpreter` |
    | A4 | `https://maps.mail.ru/osm/tools/overpass/api/interpreter` |

    The A4 server is VK Maps Overpass, listed in the OpenStreetMap wiki as a global public Overpass instance. The endpoint actually used is recorded in every `observation.json`.

    **Unchanged:**
    - OSM query text and both OSM query SHA256 values, boxes;
    - the request: `POST` with body `data=<exact query>`, the A2 User-Agent;
    - source identity (`osm`);
    - the pipeline: categories, normalizer, ER, field policies;
    - the evaluation: sampling, labelling;
    - the acquisition rules: observation atomicity and success criteria.

    The frozen Wikidata observations (artifacts 11537783193 and 11538206827) stay untouched. All runs against `overpass-api.de` and the A3 runs are failure evidence only and are excluded from `raw_v1`.

    **Execution of A4.** Only `osm/kotor` and `osm/budva`, dispatched manually and run sequentially, one request each, with no automatic retry.
    - A success is frozen immediately and never fetched again.
    - A failure keeps its raw body and metadata; activity stops, and no other endpoint or run is tried automatically.
    - If both succeed, the process stops at the two artifacts. No `pilot_run` is run and the dataset is not inspected.

    **Limit on endpoint changes.** A4 is the last change of public Overpass instance. If A4 also fails, the next step is not another server. Instead, the two OSM requests are made from an ordinary external machine under the A4 protocol and their raw bodies are imported. That separates "public Overpass instances cannot handle the query" from "GitHub-hosted runners / their IPs are poorly served".

**This is the pre-data checkpoint.** Methodology is frozen at the commit that adds the blind-labelling rule (on top of 9ded138 and db5c12c). No further backend code before the four response bodies exist.

**Fixed sequence:**

1. Four untouched response bodies (OSM and Wikidata × Kotor and Budva).
2. sha256 freeze.
3. Ingest with the frozen 5.1 rules.
4. **RAW METRICS** (`raw_v1`).
5. Deterministic audit sample → technical audit (kept aside) + **blind view**.
6. HUMAN LABELS on the blind view → **labels file frozen** (sha256).
7. Technical audit + labels → precision per stratum / false merges / false splits / ambiguity analysis.
8. Classify the real defect classes.
9. Only then any code change, after a regression fixture made from the real case. The full regression suite is run then, not before.

## Method (raw first, stricter than before)

1. **Fetch** (`tools/pilot_fetch.py`, the only networked step). Exact response bodies are kept with the query, fetch time and sha256. This is raw data preservation.
2. **Ingest offline** (`tools/pilot_run.py`) with the **frozen Iteration 5.1 rules**:
   - `RawDumpAdapter` serves source-native records;
   - `osm.overpass.v1` and `wikidata.sparql.v1` map fields without inventing values (`app/world/real_formats.py`);
   - the generic normalizer does the rest and records every issue (unparsed hours, unmapped category, several phones in one tag).
3. **Report before touching anything.** The runner reports:
   - field coverage and identity-signal coverage per source;
   - MATCH / AMBIGUOUS / NO_MATCH per pair and per rule;
   - evidence-state distribution;
   - candidate-set p50 / p95 / p99 / max;
   - fact agreement / conflict / single-source / contested rates per field, with winner sources;
   - an opening-hours audit;
   - multilingual alias counts by language and script;
   - discovery samples;
   - performance;
   - a WorldSnapshot.
4. **Human audit:** `out/audit_pairs.csv` and `.jsonl`. They contain:
   - 50 random MATCH, 50 AMBIGUOUS and 30 difficult NO_MATCH pairs;
   - **all** unusual merges (name < 0.5 or > 150 m);
   - **all** pairs with a contradicting signal.

   Every row shows both sides' names, addresses, coordinates and distance, phones, websites, ids, categories, evidence states, the decision and its rule, and (in `.jsonl`) the raw records. A person fills `label` with SAME_ENTITY, DIFFERENT_ENTITY or UNSURE.
5. **Same-source look-alikes.** ER never compares two records of one source, by design. Duplicate map listings inside OSM are therefore invisible to it, so `out/same_source_lookalikes.csv` lists them for review.
6. **Score** (`tools/pilot_labels.py`): precision on the random MATCH sample, false merges, false splits, and how AMBIGUOUS splits under review. Labels stay in their own file.
7. **Only then** classify real duplicate classes and real defects. Each one becomes a regression fixture built from the real case, and only after that a code change (§15 of the brief).

## How to run

```
python tools/pilot_fetch.py                    # needs network access to the two endpoints
python tools/pilot_run.py [--db postgresql+psycopg://...] [--now ISO]       # writes out/raw_v1/ once
# label out/raw_v1/audit_blind.csv (human_label column) or write data/pilot/kotor_budva/labels_raw_v1.csv
python tools/pilot_labels.py --run raw_v1
```

## Code changes in Iteration 6 so far (before any real data)

These are additions only. ER rules, thresholds, blocking and policies are unchanged.

- `app/world/real_formats.py` and two formats in `normalize.py`: OSM and Wikidata field mapping.
- `RawDumpAdapter`: a real source read from a saved dump.
- `matching.PAIR_LOG`: an opt-in observer of every pairwise decision, with the candidate reason. It never changes an outcome.
- `tools/pilot_fetch.py`, `tools/pilot_run.py`, `tools/pilot_labels.py`.
- `tests/test_real_formats.py`: wire-format and tooling tests on hand-written records. These are format fixtures, not real places, and say nothing about ER quality.
