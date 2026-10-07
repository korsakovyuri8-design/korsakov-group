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
   - **Sample size.** Each stratum gets min(size, max(10, ⌈quota × share⌉)), with quotas 50 / 50 / 30. Easy explicit-id matches therefore cannot crowd out contact or fuzzy merges.
   - **Census.** Unusual merges and every pair with a contradicting signal are audited in full.
   - **Scoring.** `pilot_labels.py` reports precision **per MATCH stratum** and a population-weighted estimate, and how each AMBIGUOUS stratum splits under review. Census and difficult groups find errors; they do not estimate rates.
   - **Reproducibility check.** Two independent runs on the same dumps and code produce the same sample (tested).
7. **`fetched_at` is the moment of the actual HTTP request**, not of the import. It must carry a UTC offset, lie in the past, and not precede the data's own timestamp (Overpass `timestamp_osm_base`). Otherwise the import is refused.

**This is the pre-data checkpoint.** Methodology is frozen at the commit that adds this section, on top of db5c12c. No further backend code before the four response bodies exist.

**Fixed sequence:**

1. Four untouched response bodies (OSM and Wikidata × Kotor and Budva).
2. sha256 freeze.
3. Ingest with the frozen 5.1 rules.
4. **RAW METRICS** (`raw_v1`).
5. Audit sample export.
6. HUMAN LABELS.
7. Precision / false merges / ambiguity analysis.
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
# label out/raw_v1/audit_pairs.csv (label column) or write data/pilot/kotor_budva/labels_raw_v1.csv
python tools/pilot_labels.py --run raw_v1
```

## Code changes in Iteration 6 so far (before any real data)

These are additions only. ER rules, thresholds, blocking and policies are unchanged.

- `app/world/real_formats.py` and two formats in `normalize.py`: OSM and Wikidata field mapping.
- `RawDumpAdapter`: a real source read from a saved dump.
- `matching.PAIR_LOG`: an opt-in observer of every pairwise decision, with the candidate reason. It never changes an outcome.
- `tools/pilot_fetch.py`, `tools/pilot_run.py`, `tools/pilot_labels.py`.
- `tests/test_real_formats.py`: wire-format and tooling tests on hand-written records. These are format fixtures, not real places, and say nothing about ER quality.
