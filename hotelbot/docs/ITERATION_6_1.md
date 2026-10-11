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
