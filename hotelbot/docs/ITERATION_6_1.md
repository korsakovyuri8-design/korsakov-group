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
