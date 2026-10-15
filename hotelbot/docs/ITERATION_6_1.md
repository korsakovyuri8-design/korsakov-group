# Iteration 6.1 — Real-world hardening

Source of every defect: the frozen `raw_v1` run (`data/pilot/kotor_budva/out/raw_v1/`, inputs frozen at 0cb918b).
`raw_v1` is neither modified nor rerun, and ER is unchanged. Before/after metrics are computed on the same frozen inputs into a new run directory.

**Human labelling is deferred.** It is a separate research branch: the annotator package stays frozen in `annotator_package_raw_v1/` (6cd89ae) and the §16/§17 procedure in `REAL_PILOT.md` still applies whenever labels arrive. No ER precision claims are made until then.

Priority order: (1) opening-hours parser, (2) taxonomy gaps, (3) discovery NLU, (4) unnamed OSM entities, (5) same-source lookalike diagnostics, (6) second-source blocking.

Per defect: reproduce from `raw_v1` → define the class → failing tests first → narrowest general fix → full tests/evals → before/after on the frozen dataset. There is no per-example tuning without a general rule.

## 1. Opening hours — classification of the 71 unparsed strings (no code changed)

Source: `out/raw_v1/hours_audit.csv`, `kind=unparsed`. That gives 71 strings: 362 parsed, 71 not.
Parser: `app/world/normalize.py::parse_osm_hours`. Each rule must match `^<day selector>\s+<times|off|closed>$` and rules are split on `;` only.

### Should already work (the parser claims to read OSM `opening_hours`) — 41

**A. Bare time span without a day selector — 41 strings (58 %).** Examples: `09:00-22:00`, `12:00-23:00`, `07:00-01:00`, `08:00-24:00`, `7:00-24:00`.

In OSM a rule with no day selector applies every day. The regex demands a leading day selector, so the whole class fails. Verified: `09:00-22:00` fails, while `Mo-Su 09:00-22:00` parses to the intended result, past midnight and `24:00` included. A single systemic bug explains every string in the class.

### Valid OSM, reasonable extensions — 21

**B1. `PH` merged into an all-days selector — 5.** Examples: `Mo-Su,PH 07:00-22:00`, `PH,Mo-Su 07:00-23:00`. Mo-Su already covers every date, so the holidays add nothing. The result is exact without a holiday calendar.

**B2. `PH` with its own rule — 10.** Examples: `Mo-Sa 07:30-23:00; Su off; PH off`, `Mo-Sa 07:30-21:00; PH unknown`, `Mo-Fr 08:00-18:00; Sa,Su,PH 09:00-18:00`.

The weekday part parses, but the holiday rule needs a public-holiday calendar for Montenegro, which we do not have. Dropping it would give a **wrong** "open" on public holidays.

Options to decide:
- (a) store the weekday hours plus the raw PH rule, and return UNKNOWN on dates that may be holidays — this needs a calendar anyway;
- (b) add an ME public-holiday calendar to `special`;
- (c) keep the whole string UNKNOWN as today.

Recommendation: (c) now, (b) later as a separate task. The B2 strings stay unknown in this round.

**C. `,` as rule separator — 4.** Examples: `Mo-Fr 08:00-20:00, Sa 08:00-15:00`, `Mo-Fr 07:00-01:00, Sa,Su 07:00-02:00`.

In the OSM spec `, ` between full rules is an *additional* rule: it adds and does not override. The parser only splits on `;`. One string, `Tu-Th,Su 14:00-24:00, Fr-Su 14:00-01:00`, names Sunday twice. Per the spec that is a union, giving Su 14:00-01:00. It is flagged for an explicit test.

**D. Month-range selectors — 2.** Examples: `07:00-01:00; Dec-Feb 07:00-24:00`, `Sep-May Mo-Sa 08:00-21:00; Sep-May Su 09:00-16:00; Jun-Aug: Mo-Su 08:00-22:00`.

These map to the existing `seasonal` model (MM-DD ranges, wrapping the year). Valid OSM, but more grammar: month ranges, rule override order, and the optional `:` after a wide-range selector. Lower priority than A and C.

### Malformed or ambiguous source data — keep UNKNOWN — 9

**E1. Free-text seasons — 4.** `Winter 10:00-24:00; summer 10:00-02:00`, `08:00-23:00 winter; 08:00-24:00 summer`, `off season closed` ×2. Winter and summer have no boundaries, so they cannot be mapped without guessing.

**E2. `closed` alone — 5.** `closed` ×4, `"closed"` ×1 (quoted). This is not a schedule. It could mean permanently or temporarily closed or a disused feature. Hours stay UNKNOWN. A closure is not inferred from it; it may later feed a "possibly closed — review" signal.

### Summary

| Class | n | Disposition |
|---|---:|---|
| A bare time span | 41 | **bug** — fix |
| B1 PH + all days | 5 | extension, exact — fix |
| C `,` additional rule | 4 | extension (spec) — fix |
| D month ranges | 2 | extension — later, maps to `seasonal` |
| B2 PH own rule | 10 | needs holiday calendar — stay UNKNOWN for now |
| E1 free-text season | 4 | stay UNKNOWN |
| E2 `closed` alone | 5 | stay UNKNOWN (possible review signal) |
| **total** | **71** | |

Expected effect of fixing A + B1 + C only: parsed goes from 362 to **412 of 433** (83.6 % → 95.2 %), with no new interpretation that the spec does not define. Adding D gives 414. The remaining 19 stay UNKNOWN on purpose.

**Side observation, not counted above.** `7:00` is accepted but stored unpadded (`"7:00"`). It works with `_t()`, but any string comparison of times would misorder it. The fix should normalise every time to `HH:MM`.

### Fix and before/after (A + B1 + C + HH:MM; D, B2, E untouched)

**Commits.**
- e75d634 — failing tests first: 11 fail before the fix; the B2/D/E guards and the `;` override test pass before and after.
- eb88150 — the `parse_osm_hours` fix.
- e5bed38 — run `out/hours_fix_v1/` on the same frozen inputs. Code `eb88150`, `code_dirty: false`, dump hashes identical to `raw_v1`. `raw_v1` is not touched.

**Opening hours.**

| | raw_v1 | hours_fix_v1 |
|---|---:|---:|
| opening_hours parsed | 362 / 433 (83.6 %) | **412 / 433 (95.2 %)** |
| unparsed | 71 | 21 |
| "opening hours not understood" issues | 72 | 21 |

The issue count includes one `opening_hours:kitchen` string, `09:00-22:00` (class A), which is now parsed as well.

**Gain by class.**
- Added: A +41, B1 +5, C +4 (= 50).
- Still unparsed: B2 10, E1 4, E2 5 (the 19 deliberately unsupported strings), plus D 2.

All 21 remaining strings were checked by value against the classification, and none of the 19 became parsed.

**No regressions among old parses.** The old (HEAD~) and new parsers were run on all 866 `opening_hours` tag values in the frozen OSM dump (each place appears twice: per-box part and merged). Every value parsed before is still parsed, with identical minute-level semantics. 4 values changed only by `7:00` → `07:00`.

**ER and everything else unchanged.** These are identical to `raw_v1`: pairs and outcomes, world counts, candidates per record, same-source lookalikes, fact resolution, ingest decisions, the audit block and `audit_blind.csv` (sha256 `3ba7730e…`).

**User-visible effect (discovery sample).**
- `state_unknown` rejections fall from 241 to 220.
- `closed_at_time` rises from 16 to 19: places now correctly known to be closed.
- "Restaurant in Kotor open tonight" and "eat late in Budva" now surface places whose hours were bare spans: Pepe Nero, Pržun, Astoria, Rivijera.

**Tests.** 782 collected, 781 pass. The one failure is `test_local_layer.py::test_staff_can_read_a_guest_plan`, which also fails without this change (verified by stash). It is date-dependent ("events this week") and unrelated.

## 2. Taxonomy gaps — classification of the 155 unclassified OSM records (no code changed)

**Source.** The frozen OSM dump: 2064 unique elements.

**How the category is chosen today.** `real_formats.osm_category()` takes the **first present** key in the order `amenity, tourism, shop, historic, leisure, natural, man_made, office`. That `key=value` is looked up in `pilot.yaml` `category_map`. If it is not there, the record becomes `unclassified`.

There are 155 records, in 13 patterns.

**How each record got into the dataset.** It was attributed to the Overpass clause that matched its tags.

**Existing vocabulary.** `app/places/taxonomy.py` has `historic_site` (CULTURE), `monument`, `supermarket`, `souvenir_shop`, `bike_rental`, `car_rental` and `ski_rental` (RENTAL), and `visitor_center` (top category ATTRACTION). It has **no** generic attraction, convenience, artwork or boat subcategory.

| raw tag | records (named) | how it entered | existing internal category | action | confidence |
|---|---:|---|---|---|---|
| `shop=convenience` | 76 (52) | **requested** (`shop~…convenience…`) | none. `supermarket` is a sibling, not a broader type | **ADD_CATEGORY** `convenience_store` (SHOPPING) | high |
| `tourism=attraction` | 37 (25) | **requested** (`tourism~…attraction…`) | top category ATTRACTION exists; no generic subcategory | **ADD_CATEGORY** generic `attraction` (ATTRACTION) for 36. Never narrowed to museum or viewpoint. 1 record → NORMALIZER_DEFECT (row below) | high |
| `tourism=artwork` | 11 (5) | **requested** | none. `monument` is not the same thing (murals, sculptures) | **ADD_CATEGORY** `artwork` (CULTURE) | medium |
| `amenity=boat_rental` | 4 (3) | **requested** | the RENTAL family exists, with no boat entry | **ADD_CATEGORY** `boat_rental` (RENTAL) | high |
| `historic=city_gate` | 11 (10) | broad `nwr["historic"]` | `historic_site` (broader, not narrower) | **MAP_EXISTING** → `historic_site` | high |
| `historic=tomb` | 3 (2) | broad `historic` | `historic_site` | **MAP_EXISTING** → `historic_site` | high |
| `historic=yes` | 5 (4) | broad `historic` | `historic_site`. OSM only says "historic" | **MAP_EXISTING** → `historic_site` | medium |
| `historic=ship` | 2 (2) | broad `historic` | `historic_site` (historic object, not a site) | **MAP_EXISTING** → `historic_site` | medium |
| `historic=cannon` | 2 (0) | broad `historic` | `historic_site` | **MAP_EXISTING** → `historic_site` | medium |
| `historic=pillory` | 1 (0) | broad `historic` | `historic_site` | **MAP_EXISTING** → `historic_site` | medium |
| `historic=building` | 1 (0) | broad `historic` | `historic_site` | **MAP_EXISTING** → `historic_site` | medium |
| `tourism=yes` | 1 (1) | via **`natural=beach`** | `beach` (from `natural=beach`, already mapped) | **NORMALIZER_DEFECT** | high |
| `amenity=drinking_water` | 1 (0) | via **`historic=memorial`** | `monument` (from `historic=memorial`, already mapped) | **NORMALIZER_DEFECT** | high |
| (subset of `tourism=attraction`) | 1 | via `tourism=attraction` **and** `historic=ruins` | `historic_site` (from `historic=ruins`, mapped) | **NORMALIZER_DEFECT** | high |

**NORMALIZER_DEFECT, 3 records.** The selection uses key order only. An incidental tag (`amenity=drinking_water`, `tourism=yes`, `tourism=attraction`) wins over a **mapped** tag on the same element. In two of the cases the mapped tag is the very one the query requested.

Proposed general rule: choose the first tag in key order **that the source's `category_map` knows**. Fall back to the first present tag (and `unclassified`) only when no tag is mapped. Records that already have a mapped first tag keep their category.

Checked: only 2 mapped records carry a second, different mappable tag:
- `amenity=place_of_worship` + `historic=ruins`;
- `amenity=bar` + `leisure=beach_resort`.

Both keep their current, sensible category under the rule.

**KEEP_UNCLASSIFIED.** None of the 13 patterns. `historic=yes` is the closest candidate; it is mapped only to the broad `historic_site`, never to anything narrower.

The 2 `tourism=attraction` records that also carry `natural=tree` stay `attraction`, because `natural=tree` is not mapped.

### Baseline

| step | records leaving `unclassified` | of 155 |
|---|---:|---:|
| obvious MAP_EXISTING only (`city_gate`, `tomb` → `historic_site`) | **14** | 9.0 % |
| + medium MAP_EXISTING (`historic` tail: `yes`, `ship`, `cannon`, `pillory`, `building`) | 25 | 16.1 % |
| + NORMALIZER_DEFECT fix | 28 | 18.1 % |
| + ADD_CATEGORY (`convenience_store` 76, `attraction` 36, `artwork` 11, `boat_rental` 4) | 155 | 100 % |

**Most of the gap is ADD_CATEGORY.** It covers 127 of 155 records, and all of them are values we requested ourselves in the query and forgot to map. Mapping alone cannot close it without inventing a narrower or sibling category.

### Fix and before/after — three pieces, each tests-first

| piece | tests | change |
|---|---|---|
| tag selection | 37bda41 | a7a8440 — first mapped **specific** tag in key order; generic (`tourism=attraction`, `tourism=yes`, `historic=yes`) only as fallback; `unclassified` only if nothing is mapped |
| historic values | 5986544 | 25e4e89 — 7 explicit `historic=<value>: historic_site`, no wildcard |
| new subcategories | a178fbb | 8bb47ef — `convenience_store` (SHOPPING), `tourist_attraction` (ATTRACTION), `artwork` (CULTURE), `boat_rental` (RENTAL); en/cnr/ru labels, **no search keywords**; 4 pilot mappings |

**Run `out/taxonomy_fix_v1/`.** Code 8bb47ef, `code_dirty: false`, the same dump hashes. Compared against `hours_fix_v1`.

**Categories.**
- `category_mapped` goes from 1909 to **2064 of 2064**, and the unmapped count from 155 to **0**.
- Per record:
  - 25 records via the explicit `historic` maps;
  - 3 via the selection fix (`tourism=yes` → `beach`, `amenity=drinking_water` → `monument`, `tourism=attraction` + `historic=ruins` → `historic_site`);
  - 127 via the new subcategories (`convenience_store` 76, `tourist_attraction` 36, `artwork` 11, `boat_rental` 4).
- Of the 1909 records classified before, **none** changed subcategory. This was checked per record against the raw_v1 selection and map.

**ER: one decision changed. The acceptance criterion "ER pair decisions unchanged" is NOT met.**

| | hours_fix_v1 | taxonomy_fix_v1 |
|---|---:|---:|
| MATCH | 12 | **11** |
| AMBIGUOUS | 13 | **14** |
| NO_MATCH | 5062 | 5069 |
| pairs evaluated | 5087 | 5094 |
| multi-source canonicals | 12 | 11 |

**The pair.** Wikidata `Q4981066`, "Church of Our Lady of Remedy" (`religious_site`, CULTURE), against OSM `way/207434563`, "Crkva Gospe od Zdravlja". The two share the id `wikidata=Q4981066`, lie 15 m apart and have name similarity 1.0.

The OSM element has **no** `amenity=place_of_worship`. Its only category tag is `tourism=attraction`.
- Before: `unclassified`, so the top category was OTHER, which is exempt from the category check. Result: MATCH.
- Now: `tourist_attraction`, so the top category is ATTRACTION, which differs from CULTURE. `category_conflict=true` gives the rule "shared id but the records contradict each other", so the pair is AMBIGUOUS.

That is the only changed outcome. The +7 evaluated pairs (all NO_MATCH) come from the now-separate canonical. 178 NO_MATCH pairs changed their rule label from `-` to "different kind of business" with the same outcome. The audit sample changed with it: the `match:explicit_id_only` stratum goes from 11 to 10 and `ambiguous:other` from 2 to 3. The `taxonomy_fix_v1/audit_blind.csv` hash differs. `raw_v1` and its blind file are untouched.

**Diagnosis.** This is the "specific beats generic" rule failing to reach ER. `tourism=attraction` says nothing about what the object is, so it cannot *contradict* `religious_site`. Under the 5.1 principle that a missing value is UNKNOWN and never CONTRADICTS, a generic category should behave like OTHER in `matching._decide_place`. The fix would sit in ER, which this step was not allowed to touch, so it is left for a decision.

### ER: generic subcategory = UNKNOWN for the type check (decision A, narrow form)

**Tests first.** 11f8168: 5 of 6 tests fail before the change; the `visitor_center` guard passes both before and after.

**The change.** a8f61ce, in `matching._decide_place`. `GENERIC_SUBCATEGORIES = {"tourist_attraction"}` — explicit, not all of ATTRACTION, no wildcard. The category-conflict check now compares only **specific** subcategories, on both sides of the pair. A generic tag alone neither supports nor contradicts. A specific subcategory on the same canonical (e.g. `{tourist_attraction, religious_site}` against `restaurant`) still conflicts. For data without generic subcategories the check is identical to before.

**Tests cover:**
1. attraction + religious_site with a shared id → MATCH;
2. the same with the sources in reverse order (symmetry);
3. a generic tag alone gives no conflict;
4. a canonical `{tourist_attraction, religious_site}` against `restaurant` still conflicts;
5. `visitor_center` is not generic.

All earlier ER safety tests pass.

**Run `out/taxonomy_fix_v2/`.** Code a8f61ce, `code_dirty: false`, the same dump hashes. `taxonomy_fix_v1` is kept as the record of the defect.

| | hours_fix_v1 | taxonomy_fix_v1 | **taxonomy_fix_v2** |
|---|---:|---:|---:|
| MATCH | 12 | 11 | **12** |
| AMBIGUOUS | 13 | 14 | **13** |
| NO_MATCH | 5062 | 5069 | **5062** |
| category_mapped | 1909 | 2064 | **2064** |

**Pair-level check.**
- The ingest was rerun with the `hours_fix_v1` code (eb88150) and its own `pilot.yaml`, and with the current code and current `pilot.yaml`, on the same frozen dumps.
- All **5087** pair decisions (record → canonical members → outcome) are **identical**.
- `Q4981066` ↔ `way/207434563` is MATCH again.

**Against `hours_fix_v1`, unchanged:**
- world counts;
- candidates;
- facts;
- the audit strata;
- same-source lookalikes;
- opening hours;
- ingest decisions.

**What did change:**
- 71 NO_MATCH pairs changed their rule label from `-` to "different kind of business", with the same outcome. The newly classified records now carry a real kind.
- In `audit_blind.csv` the same 55 pair_ids appear in the same order. Only the `category_b` column differs, for records that were `unclassified` before.

**Lesson from real data.** ER had assumed that any assigned top category is equally strong identity evidence. A richer taxonomy showed this is false for generic tags.

## 3. Discovery — diagnosis on the current state (hours + taxonomy + ER fixed), no code changed

**Setup.**
- The world was rebuilt from the frozen dumps with code c347c6a, the same state as `taxonomy_fix_v2`: 2078 active places.
- The evaluation time is the newest fetch: 2026-10-08 09:17 UTC, i.e. 11:17 local.
- Each query went through `parse_discovery(require_cue=False)`, then `run()`, with the pilot's fixed anchor point (old-town gate) and `limit=5`, exactly as `pilot_run.discovery_sample` does.
- Three extra *counterfactual* queries, using existing vocabulary, show what happens once understanding succeeds.

### Path per query

**1. "Find a restaurant in Kotor open tonight"**
- Built: subs `{restaurant, konoba, fast_food}`, `at` = 22:00, `open_at`.
- Retrieved 328. Rejected: `closed_at_time` 19, `state_unknown` 220.
- Top 5: all open at 22:00, within 0.6 km.
- Correct. Policy: once known-open places exist, unknown ones are not used to pad the list.

**2. "Where can I get cocktails in Budva?"**
- Built: label `drinks`, subs `{cocktail_bar, lounge, rooftop}` only.
- Retrieved **0**.
- The data holds **0** `cocktail_bar` / `lounge` / `rooftop` places: OSM has no such kinds, and `amenity=bar` → `bar`. There are 48 `bar`, 14 `pub` and 10 `nightclub`, of which 41 lie within 3 km of Budva's old town. The raw OSM has 2 elements with explicit cocktail evidence (`Strix Cocktail Bar` and `THE DEEP`, both `amenity=bar`).
- **Not** an understanding failure: "cocktails" was recognised. **Not** a coverage gap: bars exist, and cocktail evidence even exists.
- The query is built from kinds the data never emits, with no fallback to the bar family.
- The hours, taxonomy and ER fixes had no effect: raw_v1 also gave 0, with the same query.
- Counterfactual "a drink in Budva" → NIGHTLIFE, 72 retrieved, sensible bars.

**3. "Nearest pharmacy"**
- Built: subs `{pharmacy}`, essential, no time, no radius.
- Retrieved 32.
- Top 5 at 0.12 / 0.44 / **6.5 / 15.7 / 16.0 km**. All are known-open (rank key: availability first, then distance).
- The *nearest* pharmacy (0.08 km, unknown hours, unnamed) and others at 0.39 and 1.37 km are pushed below places 16 km away.

**4. "Something interesting near the Old Town"**
- `parse_discovery` → **None**.
- "interesting" is no cue and names no kind. "near the old town" is cut out as the anchor phrase, so nothing is left.
- Counterfactual "Something to see near the Old Town" → label `sights`, categories `{ATTRACTION, CULTURE, NATURE}`, `anchor_text` "old town", **no radius**.
  - Retrieved 450 across the whole region.
  - Top 5: Muzej mačaka 0.09 km, Galerija nives 0.09, Naval Heritage 6.5, **Malaya Beach Club 16.1**, **Arheološki muzej 17.2**.
  - Meanwhile 63 CULTURE / ATTRACTION places lie within 1 km of Kotor's old town (23 historic sites, 19 religious, 6 tourist attractions, 6 monuments, …).
- So understanding is the first wall, and ranking is the second.

**5. "Where can we eat late?"**
- Built: label `food`, subs `{restaurant, konoba, fast_food}`, **at = None**: "late" is not understood (only "open late", "after midnight", "after N" are).
- Retrieved 328.
- Top 5: open *now* (11:17), close 23:00–01:00.
- Plausible by accident. The time constraint was dropped.

**6. "Is there an ATM nearby?"**
- Built: subs `{atm}`, essential, `max_km` 2.0.
- Retrieved 67.
- Top 2 are 24/7 ATMs at **0.36 and 0.78 km**, ranked above at least 8 ATMs within **40–90 m** with unknown hours.
- 4 of the 5 lines render as `node/…`: **97 of 103 ATMs** are unnamed.
- Euronet and `node/13716103405` sit at the same spot (same-source lookalikes, step 5).

**7. "A museum in Kotor"**
- Built: subs `{museum}`. "in Kotor" is **not** a constraint (no locality is extracted at all), and there is no radius.
- Retrieved 6, which is all the museums in the region.
- Top 5 in order:
  - Muzej mačaka 0.09 km;
  - **Naval Heritage 6.5 km**;
  - **Arheološki muzej 17.2 km** (Budva);
  - Museum Maritimum 0.04 km (unknown hours);
  - `way/305481242` 0.08 km (unnamed).
- Coverage is thin: 6 museums, 3 of them in Kotor. But the ordering is the defect, since a Budva museum outranks the Maritime Museum in Kotor's old town.

**8. "Find a beach in Budva"**
- Built: subs `{beach}`, no time.
- Top 1 is **Malaya Beach Club, 2.0 km** (it has hours, open). It outranks City beach at 0.1 km, Ričardova glava at 0.12 and Pizana at 0.15, all with unknown hours. Beaches almost never carry hours.

### Systemic finding

With **no time in the request**, `rank()` puts `availability` (OPEN 0 < OPEN_LATER 1 < UNKNOWN 2 < CLOSED 3) **before distance**, for essential and non-essential queries alike.

Hours are known for only about 20 % of OSM places (412 of 2064 parsed). So "known open" beats "nearby" across the board: pharmacies (16 km), museums (17 km), beaches (2 km against 0.1 km), sights (16–17 km), ATMs (0.78 km against 0.04 km).

The hard filter already treats unknown correctly: only KNOWN-closed is removed. The *ranking* still treats UNKNOWN almost like CLOSED.

**Unnamed entities.** 968 of 2078 places are rendered as `node/…` / `way/…`:
- `car_park` 384;
- `viewpoint` 98;
- `atm` 97;
- `tourist_info` 52;
- `beach` 39;
- `souvenir_shop` 35;
- `public_toilet` 34;
- `historic_site` 28;
- `convenience_store` 24;
- `restaurant` 20;
- …

### Table

| Query | UNDERSTAND | Query built | Retrieved | Top result quality | Primary defect | Proposed fix class |
|---|---|---|---:|---|---|---|
| Find a restaurant in Kotor open tonight | ok (locality ignored, harmless here) | restaurants, open at 22:00 | 328 | good | — | — |
| Where can I get cocktails in Budva? | ok ("drinks / cocktails") | only cocktail_bar / lounge / rooftop | 0 | none | **OVERCONSTRAINED_QUERY** | query building: "cocktails" → bar family plus soft cocktail relevance (name / tag evidence), not an empty kind set |
| Nearest pharmacy | ok | pharmacy, essential | 32 | 3 of 5 are 6–16 km away; nearest (80 m) missing | **RANKING_FAIL** | with no time asked, unknown ≠ closed: distance before (open vs unknown), for essential kinds at least |
| Something interesting near the Old Town | **fail** (None) | — | — | — | **UNDERSTAND_FAIL** | intent: "interesting / something to do" → sights. Then anchor resolution ("old town" → point + radius). Then the ranking fix |
| Where can we eat late? | partial ("late" dropped) | food, no time | 328 | plausible by accident | **UNDERSTAND_FAIL** (time constraint) | temporal: "late" → open_until / evening |
| Is there an ATM nearby? | ok | atm, essential, 2 km | 67 | 0.36 / 0.78 km ahead of 40 m; `node/…` names | **RANKING_FAIL** + **RENDER_FAIL** | ranking fix (as for pharmacy); neutral display name for unnamed entities |
| A museum in Kotor | partial ("in Kotor" not a constraint) | museum, whole region | 6 | 6.5 / 17 km above 40 m | **RANKING_FAIL** (+ locality, + unnamed) | ranking fix; locality "in <town>" → anchor/area; render |
| Find a beach in Budva | ok (locality ignored) | beach | 69 | 2 km beach club above 0.1 km beaches | **RANKING_FAIL** | ranking fix |

**Answer to the cocktail question.** It is neither NLU nor coverage. Understanding works, and the data has 41 bars nearby and 2 with explicit cocktail evidence. The **query builder** maps "cocktails" to three kinds no real source here produces and drops the bar family. The case was untouched by the hours, taxonomy and ER fixes (raw_v1 also gave 0).

**Order suggested by impact.**
1. RANKING: unknown hours in no-time queries. This affects 5 of the 8 sample queries.
2. OVERCONSTRAINED_QUERY: cocktails.
3. UNDERSTAND: "interesting", "late", locality "in <town>".
4. RENDER: unnamed entities.

None of these needs `SUBCATEGORIES` labels or keywords to change:
- the GENERIC phrase table and the ranking key live in `nlu.py` / `engine.py`;
- ER reads only `SUBCATEGORIES`.

Still, the 5087-pair check is repeated after each commit, as agreed.

### Ranking fix: availability first only with a time intent

**Commits.**
- d394b53 — tests first: 5 of 11 fail before the fix, the 6 controls pass.
- 51d3404 — `rank()` uses 4 keys, and `time_intent(q)` = `open_at` / `serving_at` / `open_until` / `window_end` / an explicit `at`:

| | key |
|---|---|
| time-sensitive essential | availability → distance → reliability |
| non-time essential | distance → reliability → availability |
| time-sensitive normal | availability → relevance → preferences → distance → reliability → … (unchanged) |
| non-time normal | relevance → preferences → distance → reliability → availability → … |

UNKNOWN is never made OPEN. The hard filter and the "no unknown padding when known-open exist" rule are unchanged.

**Run `out/ranking_fix_v1/`.** Code 51d3404, clean. Against `taxonomy_fix_v2`, these are identical:
- pairs, world, facts, audit, ingest, sources, opening hours;
- `audit_blind.csv`;
- **all 5087 pair decisions**, checked pair by pair.

Only `discovery` changes.

**Before/after on the 8 frozen queries.** Evaluated at 11:17 local; top-5 distances in km and states.

| query | before | after |
|---|---|---|
| restaurant Kotor tonight (time intent) | 0.1 0.11 0.07 0.12 0.58, all open | **unchanged** |
| cocktails Budva | 0 retrieved | 0 (next step) |
| nearest pharmacy | 0.12 0.44 **6.46 15.72 16.03**, all open | **0.08** 0.12 0.39 0.44 1.37 (U O U O U); mean 7.75 → **0.48** |
| something interesting | not understood | not understood (NLU step) |
| eat late (no time intent: "late" not understood) | 0.1 0.18 0.08 0.07 0.39, all open | 0.08 (U) 0.1 0.18 0.08 0.07; mean 0.16 → 0.10 |
| ATM nearby | **0.36 0.78** 0.04 0.04 0.11 | 0.04 0.04 0.11 0.08 0.07, all U; mean 0.27 → **0.07** |
| museum in Kotor | 0.09 **6.51 17.22** 0.04 0.08 | 0.09 **0.04 0.08** 6.51 17.22 |
| beach Budva | **2.01 (beach club)** 0.1 0.12 0.15 0.44 | 0.1 0.12 0.15 0.44 0.59 |

- UNKNOWN placed above OPEN: 11 pairs in the top-5 at 11:17. Every case is closer, or tied by distance bucket and decided on reliability.
- Known CLOSED above OPEN at 11:17: **0**.

**Stress check, same world, other times.** Run at 07, 09, 18, 22 and 00 local, no-time queries only, comparing old and new code:

| | old | new |
|---|---:|---:|
| CLOSED in top-5 | 3 | 7 |
| CLOSED above OPEN | 0 | 2 |

Both CLOSED-above-OPEN cases are "A museum in Kotor" at 18:00: Muzej mačaka (0.09 km, closed) and Naval Heritage (6.5 km, closed) above Arheološki muzej (17.2 km, Budva, open). Distance-justified under the agreed key.

**Gate regression — needs a decision.** `evals/scenarios/discovery.yaml::disc_pharmacy_sunday` (**gate**) now fails.
- "I need a pharmacy" on a Sunday, no time stated.
- The fixture's nearer pharmacy is **known CLOSED**; the expected first result is the open one.
- With "non-time essential = distance first", the known-closed nearer pharmacy ranks first.
- That is a known CLOSED dominating, not an UNKNOWN being un-penalised. The gate and test were left unchanged.

Proposed refinement, not implemented: for **essential** needs without a time, demote only **known CLOSED**. Key: `(state is CLOSED, distance, reliability, availability)`. An essential need ("I need a pharmacy / an ATM") implicitly means *now*.
- OPEN, OPEN_LATER and UNKNOWN would still rank by distance, so the 80 m pharmacy with unknown hours stays first.
- Only evidence of closure demotes.

Non-essential no-time queries would keep geography first, as agreed.

**Side findings, not changed.**
- Reliability before availability is computed from the *hours fact's* freshness even when a place has **no hours**. A place without hours can look "fresher" than one whose hours were verified 186 days ago. In "eat late", Konoba Galeb (0.08 km, U) ranks above Astoria (0.10 km, open) within the same 0.5 km bucket for this reason.
- The world snapshot `content_hash` is not reproducible across runs of identical evidence. Rows carry random UUID ids, and `_evidence` strips only `created_at` / `updated_at`. `taxonomy_fix_v2` and `ranking_fix_v1` have identical evidence but different hashes.

**Tests.** 1 new failure (the gate above), plus the pre-existing date-dependent `test_local_layer.py::test_staff_can_read_a_guest_plan`.

### Ranking fix v2: essential needs without a time demote only "definitely unavailable now"

**Commits.**
- d4e8530 — tests first: 4 new tests fail before the fix.
- cf644a2 — the fix.

**The rule.** For essential needs without an explicit time, the key is `(unavailable_now, distance, reliability, availability)`:
- `unavailable_now = 1` for CLOSED or OPEN_LATER;
- `unavailable_now = 0` for OPEN or UNKNOWN.

The other three keys are unchanged from v1.

**Tests.**
- near UNKNOWN beats a farther OPEN;
- near CLOSED loses to a farther OPEN, and to a farther UNKNOWN;
- near OPEN_LATER loses to a farther OPEN;
- the available group is distance-first: Unknown 80 m, then Open 400 m, then Closed 50 m;
- a normal no-time query may still put a near CLOSED above a very far OPEN;
- an explicit "open now" keeps strict availability first.

**Gate `disc_pharmacy_sunday`.** Green again, with the gate itself unchanged. The full suite has only the pre-existing date-dependent failure (`test_staff_can_read_a_guest_plan`).

**Run `out/ranking_fix_v2/`.** Code cf644a2, clean. Against `ranking_fix_v1`, these are identical:
- pairs, world, facts, audit, sources, opening hours, lookalikes, candidates;
- `audit_blind.csv`.

**All 5087 ER pair decisions** are identical to `hours_fix_v1`.

**8 frozen queries at 11:17.** km and state, O = open, U = unknown. The output is identical to v1: nothing is closed or opens later in these top-5s at 11:17.

| query | before (taxonomy_fix_v2) | after (ranking_fix_v2) |
|---|---|---|
| restaurant Kotor tonight | 0.1O 0.11O 0.07O 0.12O 0.58O | same |
| cocktails Budva | — | — |
| nearest pharmacy | 0.12O 0.44O 6.46O 15.72O 16.03O | 0.08U 0.12O 0.39U 0.44O 1.37U |
| something interesting | not understood | not understood |
| eat late | 0.1O 0.18O 0.08O 0.07O 0.39O | 0.08U 0.1O 0.18O 0.08O 0.07O |
| ATM nearby | 0.36O 0.78O 0.04U 0.04U 0.11U | 0.04U 0.04U 0.11U 0.08U 0.07U |
| museum in Kotor | 0.09O 6.51O 17.22O 0.04U 0.08U | 0.09O 0.04U 0.08U 6.51O 17.22O |
| beach Budva | 2.01O 0.1U 0.12U 0.15U 0.44U | 0.1U 0.12U 0.15U 0.44U 0.59U |

**Stress check.** No-time queries at 07, 09, 18, 22 and 00 local.

*Essential (pharmacy, ATM).*
- CLOSED or OPEN_LATER above OPEN or UNKNOWN: **0**.
- CLOSED or OPEN_LATER in the top-5: 6 before, **0** after.

*Normal queries (agreed: geography first).* Unavailable-now places above available ones:

| unavailable | above | count |
|---|---|---:|
| CLOSED | OPEN | 2 |
| CLOSED | UNKNOWN | 2 |
| OPEN_LATER | OPEN | 2 |
| OPEN_LATER | UNKNOWN | 6 |

Before, the count was 22, all OPEN_LATER above UNKNOWN, which was the old order by design.

Every CLOSED-above-OPEN case is "A museum in Kotor":
- Muzej mačaka, 0.09 km, closed: above Naval Heritage (6.5 km) at 09:00, and above Arheološki muzej (17.2 km, Budva) at 09:00 and 18:00;
- Naval Heritage, 6.5 km, closed: above Arheološki muzej at 18:00.

**Open, kept separate.**
- Snapshot `content_hash` depends on random UUIDs.
- The hours-reliability bonus applies to places without hours.

### Cocktails: the bar family as candidates, cocktail evidence as relevance

**Commits.**
- ff7620f — tests first: 4 of 7 fail before the fix. The `cocktail_bar` exact-match guard and the no-relabel guard pass before and after.
- a8567e3 — the fix, in `nlu.py` (query construction) and `engine.rank` (one evidence term). **No taxonomy vocabulary changed** (`SUBCATEGORIES` untouched, so `_taxonomy_type_words` and ER are unaffected). **No hard filter changed.**

**What changed.**
- **Candidates.** "cocktails / koktel / коктейл" now yields the whole bar family, `FAMILIES["bar"]`: bar, cocktail_bar, wine_bar, pub, lounge, rooftop, live_music_venue. Before, it yielded only cocktail_bar / lounge / rooftop.
- **Exact match.** `q.specific` stays `{"cocktail_bar"}`. The bar family is **not** added to it.
- **Evidence.** New `q.relevance_words = {cocktail*, koktel*, коктейл*}`. A place whose name, tags or attributes contain one of them gets +1 relevance. Absence excludes nothing, and no place changes subcategory.
- **Nightclub.** Not added. It was optional, and the cocktail case does not need it.

**Run `out/cocktails_fix_v1/`.** Code a8567e3, clean. Against `ranking_fix_v2`, these are identical:
- pairs, world, facts, audit, sources, hours, lookalikes, candidates;
- `audit_blind.csv`;
- all 5087 ER pair decisions (identical to `hours_fix_v1`).

Only the cocktail query changed among the 8 sample queries.

**Before/after, "Where can I get cocktails in Budva?"**

| | before | after |
|---|---|---|
| query | subs {cocktail_bar, lounge, rooftop} | subs = bar family; specific {cocktail_bar}; relevance_words {cocktail*, …} |
| retrieved | **0** | **62** |
| top 5 | — | **Strix Cocktail Bar** (bar, 0.07 km, unknown; relevance 1); Rakia & Wine Bar (bar, 0.07, unknown); Blues Brothers Bar (bar, 0.07, unknown); Mañana (bar, 0.09, opens later); Old Town Pub (pub, 0.03, opens later) |

**Unchanged (checked with the diagnosis script at fb06139 and at a8567e3):**
- "a drink in Budva": NIGHTLIFE, 72 retrieved, the same top 5;
- "a bar in Budva": the same query and the same top 5;
- the other 7 sample queries.

**THE DEEP: not boosted, recorded.** Its only cocktail evidence is OSM `description` = "Speakeasy cocktail bar". `real_formats.osm_to_generic` does not carry `description`, and the generic place normalizer does not store one, so the evidence never reaches the world. Carrying it would be a data-model / normalizer change and is out of scope here. The venue is in Tivat, 21.4 km from Budva and 6.5 km from Kotor, so it does not affect this query.

**Tests.** All green except the pre-existing date-dependent `test_staff_can_read_a_guest_plan`. All discovery evals and gates pass unchanged.

### NLU: "interesting", "in <town>", "late" — three separate fixes

Each fix was committed tests-first. After every code commit all **5087 ER pair decisions** were re-checked against `hours_fix_v1`: 0 differences each time.

| defect | tests | fix | rule |
|---|---|---|---|
| interesting | 8fee5a4 | 2a44488 | `interesting` / `zanimljiv*` / `интересн*` → ATTRACTION + CULTURE + NATURE (label `sights`) |
| in <town> | 6260021 | 4bf18d3 (+ e4f25d1) | a capitalised name after "in" becomes `q.locality`, resolved against the region's registered coverage areas; places outside are `rejected["outside_locality"]` |
| late | e05b718 | f2e774f | "late" for food / nightlife sets the existing "late night" constraint: `open_until` = next day 00:30. No new threshold |

**Conditions and edge cases.**
- **interesting.** Applies only when nothing more specific was asked, and when there is a discovery cue, "something / nešto / что-нибудь", or a real anchor. An events request ("anything interesting happening") is excluded. The word alone ("that's interesting", "what an interesting idea") is no request.
- **in <town>.** Areas of kind `locality` / `admin_area`, matched by name or alias. Each area uses its **own bbox or radius**; no radius is invented. With no point given, distance is measured from the area's centre. An unknown town ("in Paris") scopes nothing. "in the evening" and "in town" are not localities.
- **late.** "late check-in", "we arrive late" and "later" are unchanged. "open tonight", "after midnight" and "open now" keep their meaning; tests pin all three.

**e4f25d1** moves the locality lookup into `app/discovery/repository.py`, because discovery reads the world only through its repository (`tests/test_architecture.py`, which f2e774f had broken). Behaviour is unchanged. `nlu_fix_v1` (at f2e774f) and `nlu_fix_v2` (at e4f25d1) have identical discovery samples.

**Run `out/nlu_fix_v2/`.** Code e4f25d1, clean. Against `cocktails_fix_v1`, these are identical: pairs, world, facts, audit, sources, hours, lookalikes, candidates and `audit_blind.csv`.

**8 frozen queries, after the NLU fixes.**

| query | before (cocktails_fix_v1) | after (nlu_fix_v2) |
|---|---|---|
| restaurant in Kotor open tonight | top 5 open, ≤ 0.6 km; whole region | same top 5; `outside_locality` 197 |
| cocktails in Budva | Strix first, 62 retrieved | same top 5; `outside_locality` 27 |
| nearest pharmacy | — | unchanged |
| **something interesting near the Old Town** | **not understood** | sights: Church of Our Lady of Remedy 0.17 km, Kotorska katedrala 0.05, Kotorska tvrđava 0.02, Pima Palace 0.06, Saint Anne 0.08 |
| **eat late** | food, no time: Konoba Galeb (U), Astoria (closes 23:00), … | `open_until` 00:30; `closes_too_early` 85. Old Fisherman's Pub, Regina E, Perla, Riva Concept, Babaluu: all open until 01:00 |
| ATM nearby | — | unchanged |
| **museum in Kotor** | 6 shown, incl. Arheološki muzej (17.2 km, Budva) | `outside_locality` 2 (both Budva). Muzej mačaka 0.09, Museum Maritimum 0.04, `way/305481242` 0.08, Naval Heritage 6.5 km |
| beach in Budva | — | same top 5; `outside_locality` 17 |

**Known limit (data, not NLU).** The pilot's Kotor box `[42.400, 18.690, 42.470, 18.790]` also covers Tivat's Porto Montenegro. So Naval Heritage (Tivat) counts as "in Kotor". It ranks below the three old-town museums.

**Tests.** All green except the pre-existing date-dependent `test_staff_can_read_a_guest_plan`. Discovery gates are unchanged and pass.

### Unnamed entities: display name in the renderer only

**Commits.**
- ec05b89 — tests first: the import of `display_name` fails before the fix.
- 7886cd0 — the fix.

**The rule.** `render.display_name(place, locale)` handles two cases:
- **Real name:** shown unchanged. Names like "Node Cafe", "node/abc" and "Way Out Bar" stay as they are.
- **Missing name, or exactly `node|way|relation/<digits>`:** the localised kind from `taxonomy.label()`, capitalised, plus `, <address>` when the source has one. Nothing else is invented.

`candidate_line` drops the redundant "(kind)" for such places. `event_line` uses the same rule for the venue.

`Place.name`, the projection, ER and ranking are unchanged. Unnamed places are not demoted.

**Run `out/display_name_fix_v1/`.** Code 7886cd0, clean.
- The `discovery` results (ids and order) are byte-identical to `nlu_fix_v2`, and so is every other metric.
- All 5087 ER pair decisions are unchanged.
- Technical ids in `discovery_sample.md`: **7 → 0**.

**All 2078 active places rendered** with `candidate_line` in en / cnr / ru / cnr-Cyrl:
- 968 of them have a technical or empty name, 23 of those with an address;
- lines containing `node/…`, `way/…` or `relation/…`: **0** in every locale.

Examples:

    1. ATM - no opening-hours data; 0.0 km from budva old town (straight line, ~1 min walk)
    1. Museum, Zanatska ulica 313,314 - no opening-hours data; 0.1 km from kotor old town ...
    1. Muzej, Zanatska ulica 313,314 - nema podataka o radnom vremenu; ...
    1. Музей, Zanatska ulica 313,314 - нет данных о часах работы; ...
    1. Музеј, Zanatska ulica 313,314 - нема података о радном времену; ...

The address is source text and is not transliterated.

**Not covered: other paths that put `place.name` into guest-facing text.** Both are outside `render.py`, and both would write the shown name into the guest's plan, so a decision is needed:
- `app/trip/commands.py::_title`: the "planned / saved" replies and the itinerary item title;
- `app/trip/planner.py`: the shortlist itinerary title, `"{title}: {names}"`.

**Tests.** All green except the pre-existing date-dependent `test_staff_can_read_a_guest_plan`.

### Display name v2: one rule for every guest-facing path

**Commits.**
- 9fe19eb — tests first: they fail on import before the change.
- ebc6a0a — `display_name` / `is_unnamed` move to **`app/places/naming.py`**, so Trip does not depend on the Discovery renderer.

**Where the rule now applies:**
- `discovery/render.py`: candidate lines and event venues. It re-exports the very same function.
- `trip/commands.py`: saved, shortlisted and planned item titles and their replies, and the not-bookable / not-an-event replies. The module no longer contains `place.name`, which the test asserts.
- `trip/planner.py::shortlist_names`: the shortlist title.

**Behaviour.**
- An unnamed ATM is saved as "ATM" / "Bankomat" / "Банкомат".
- Named places and event titles are unchanged.
- Known limit: `PlanItem.title` stays a string in the language used at save time. Locale-aware titles via `place_id` come later.

**Run `out/display_name_fix_v2/`.** Code ebc6a0a, clean.
- Every metric, including `discovery`, is identical to `display_name_fix_v1`.
- All 5087 ER pair decisions are unchanged.
- 2078 places rendered in 4 locales: 0 lines with a technical id.

**Tests.** All green except the pre-existing date-dependent test.

### Snapshot content_hash: reproducible across builds

**Reproduced.** Two builds of the frozen Kotor/Budva world from the same dumps, same code (77b8a4a), gave different `content_hash` values: `27d820b5…` and `cfc244ee…`.
- **The only differing values** are runtime UUID columns: `id` in every table, plus the references to them (`canonical_entity_id`, `source_entity_id`, `merged_into_id`, …).
- **Nested UUIDs inside JSON** are also references: `match_reviews.candidates[].canonical_entity_id` (9) and `entity_links.evidence.also_similar[].canonical_entity_id` (4).
- **One nested UUID is source content:** `source_entities.raw.tags.panoramax` (1), an OSM photo id. It must stay.
- **Natural keys are unique and identical across both builds:** `(source_id, source_record_id)` and `canonical_slug`. The links compared under these keys are identical.

**Fix.**
- 7a24824 — tests first. The two-builds test fails before the fix. The guards (a semantic difference and a UUID in source content both change the hash) pass before and after. The direct representation test fails before the fix.
- fc796a2 — `_evidence` hashes a stable representation:
  - an entity is identified by its natural key: `source_entity:<source>/<record>`, `canonical:<slug>`, `area:<code>`; `world_sources.id` already is the source id;
  - every reference to a runtime row id is rewritten to that key, wherever it appears, nested JSON included;
  - ids of rows without a natural key are dropped;
  - rows are sorted by content;
  - values that merely look like UUIDs are kept.

The stored snapshot content and `restore()` are unchanged, and the round-trip test (`evidence_hash` after restore == `content_hash`) still passes.

**Result.** Two independent builds of the frozen world → `9aeca030…` both times. Run `out/snapshot_fix_v1/` (code fc796a2) → `9aeca030770c663d…` as well. All other metrics are identical to `display_name_fix_v2`.

Snapshot hashes stored in earlier runs were computed the old way and are not comparable with these.

## 4. Hours reliability when a place has no hours — classification (no code changed)

**Mechanism.** `rank()` sets `reliability = max(FRESH_RANK[f.state] for f in place_facts(...))` with FRESH 0 < AGING 1 < UNKNOWN 2 < STALE 3. The `hours` fact is classified even when the place **has no hours**. Its date is then the record's own date (`last_verified_at`), not an hours verification.

**Frozen world, all 2078 active places.** Freshness of the "hours" fact:

| | FRESH | AGING | UNKNOWN | STALE | dated by |
|---|---:|---:|---:|---:|---|
| has hours (412) | 9 | 20 | 0 | 383 | the hours' own `check_date` / verification |
| **no hours** (1666) | **40** | **58** | **938** | 630 | the record's date (no hours to verify) |

**Defect class.** The reliability of a fact that does not exist.
- 98 places without hours rank as FRESH or AGING "hours", and 938 as UNKNOWN. All of them come before every place whose real schedule is STALE, the state of 383 of the 412 places that have hours.
- So among places tied on distance, having *no* schedule beats having an old one.
- This is a reliability-component defect, not an availability one. The venue state stays UNKNOWN, correctly. The caveat text is already right: `explain()` drops the hours freshness caveat when the state is UNKNOWN.
- Observed in "eat late" before the NLU fix: Konoba Galeb (0.08 km, no hours) above Astoria (0.10 km, open, hours checked 186 days ago).

**Proposed rule, not implemented.** A fact that is absent is not "fresh". For a place without hours, the `hours` fact contributes the **STALE rank (3)**: it can never look more reliable than an existing schedule, however old. Absence is not penalised beyond that. With an equal distance bucket the tie then goes to availability, so the known-open place comes first. With a different distance bucket, distance still decides, so an unnamed ATM with no hours at 40 m stays first. Other facts (kitchen, closure) already count only when present.

### Hours reliability: no schedule ranks like stale (ranking only)

**Commits.**
- b52a139 — tests first: 2 of the new tests fail before the fix.
- 693d415 — the fix: in `rank()` only, a place with no `weekly` / `seasonal` / `special` schedule gets `reliability = max(reliability, FRESH_RANK[STALE])`.

What does not change: `place_facts()`, `FactFreshness`, the open state (still UNKNOWN), the caveats, and the wording ("no opening-hours data").

**What the tests pin.**
- No schedule with a fresh record → 3.
- Real schedules keep their rank: fresh 0, aging 1, stale 3.
- The data and the wording are untouched.
- Equal distance bucket: Astoria (real hours, 186 days old) beats Galeb (no hours).
- Distance still beats reliability: an ATM at 40 m without hours beats one at 780 m that is open.
- The open state and the unknown-padding hard rule are unchanged.

**One earlier test of this iteration was restated.** `test_availability_still_breaks_ties_at_the_same_distance` had places with *fresh* hours tie against one with no hours. Its intent is a tie-break by availability alone, so it now uses stale schedules: equal reliability, same expected order.

A new test pins the consequence of the approved key: at the same distance, with no time asked, a **fresh real schedule that says CLOSED ranks before no schedule at all**, because reliability comes before availability.

**All 2078 places, reliability component.** Neutral query, old vs new engine:

| change | places |
|---|---:|
| no schedule: 0 → 3 | 40 |
| no schedule: 1 → 3 | 58 |
| no schedule: 2 → 3 | 938 |
| **artificial rank 3, total** | **1036** |
| no schedule, already 3 (stale record) | 630 |
| real schedule, already STALE (3) | 383 |
| HoursStatus changed | **0** |
| rendered line (wording) changed | **0** |

**Run `out/reliability_fix_v1/`.** Code 693d415, clean.
- Non-discovery metrics are identical.
- The snapshot hash is again `9aeca030…`: same world, same hash.
- All 5087 ER pair decisions are unchanged.

**Ties broken by reliability in the frozen queries.** Each change below is a reorder inside one distance bucket.

| query | before | after |
|---|---|---|
| "Where can we eat?" (Budva, no time) — the Galeb case | **Konoba Galeb** (0.08, no hours), Astoria (0.10, open), Old Fisherman's Pub, Regina E, Rivijera | **Astoria**, Old Fisherman's Pub, Regina E, Rivijera, Punta Konoba (0.04, opens later) |
| nearest pharmacy (0.1 km buckets) | ATM-like order: `node/…` 0.08 U, Vegapharm 0.12 O, … | Vegapharm 0.12 O, Pharmacy 0.08 U, Tea Medica 0.44 O, Pharmacy 0.39 U, Dobrota 1.37 U |
| ATM nearby | `node/…` 0.04, Euronet 0.04, … | Euronet 0.04, ATM 0.04, … (same set) |
| beach in Budva | City beach, Ričardova glava, Pizana, Mogren, Greco | City beach, Pizana, Ričardova glava, Greco, Mogren (same set) |
| cocktails in Budva | Strix, Rakia & Wine Bar, Blues Brothers, Mañana, Old Town Pub | Strix, Mañana, Old Town Pub, Blue Cat, Blues Brothers |
| **something interesting near the Old Town** | Church of Our Lady of Remedy, Kotorska katedrala, Kotorska tvrđava, Pima Palace, Saint Anne | Muzej mačaka (open), Galerija nives (open), **Anton Luković, Bastion Bembo, Bastion Korner** |

**Finding, not fixed: ties broken by name.** Normal queries bucket distance at 0.5 km. Inside that bucket, with equal reliability and availability, the rest of the key is empty for most real places: no rating, no price, no relationship. So the final tie-break is `place.name`, in alphabetical order.

For sights, Kotor Cathedral and the Fortress (both Wikidata-linked) drop out of the top 5 in favour of alphabetically early bastions. Before this fix the same ties were decided by the record's edit date, which was no better grounded.

What is missing is a **prominence / notability signal**: Wikidata presence, `tourism=attraction`, multi-source agreement. That is a new ranking feature, so it is proposed for the next iteration rather than squeezed into 6.1.

**Tests.** All green except the pre-existing date-dependent test, which is next.
