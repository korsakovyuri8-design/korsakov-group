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

**To unblock:** add `overpass-api.de` and `query.wikidata.org` under Network access → Allowed domains in the environment settings (https://code.claude.com/docs/en/cloud-environments#network-access). Alternatively, run `tools/pilot_fetch.py` on any machine with internet access and commit the two dump files it writes. Everything after the fetch runs offline from the dumps.

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

**Licence consequence to keep in mind.** OSM's share-alike applies to a *publicly distributed* derived database. Internal use and Produced Works (answers shown to a traveller, with attribution) are fine. Publishing the merged canonical world as a database would oblige us to share it under ODbL.

## Scope

The bounding boxes are in `data/pilot/kotor_budva/pilot.yaml`:

- **Kotor:** old town, Dobrota, Škaljari, Muo.
- **Budva:** Budva, Bečići, Rafailovići, Sveti Stefan.

The categories are those the product already supports. The category maps are data (OSM `key=value` and Wikidata P31 → existing subcategories). An unmapped tag becomes `unclassified` plus an issue. Examples are `tourism=attraction` and `shop=convenience`, and Wikidata types outside the list. Those counts are reported, not hidden. No category architecture was added.

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
python tools/pilot_run.py [--db postgresql+psycopg://...] [--now ISO]
# label out/audit_pairs.csv (label column) or write data/pilot/kotor_budva/labels.csv
python tools/pilot_labels.py
```

## Code changes in Iteration 6 so far (before any real data)

These are additions only. ER rules, thresholds, blocking and policies are unchanged.

- `app/world/real_formats.py` and two formats in `normalize.py`: OSM and Wikidata field mapping.
- `RawDumpAdapter`: a real source read from a saved dump.
- `matching.PAIR_LOG`: an opt-in observer of every pairwise decision. It never changes an outcome.
- `tools/pilot_fetch.py`, `tools/pilot_run.py`, `tools/pilot_labels.py`.
- `tests/test_real_formats.py`: wire-format and tooling tests on hand-written records. These are format fixtures, not real places, and say nothing about ER quality.
