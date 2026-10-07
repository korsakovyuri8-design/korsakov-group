"""Iteration 6: run the Kotor + Budva real-data pilot OFFLINE from raw dumps.

    python tools/pilot_run.py [--pilot data/pilot/kotor_budva] [--db URL] [--now ISO]

RAW FIRST: the frozen Iteration 5.1 rules are used as they are. This tool
OBSERVES and reports; it changes nothing in ER, policies or thresholds.

Outputs (in <pilot>/out/<run>/, WRITE-ONCE - the first raw report is frozen
before any change to category maps, normalizer, ER or policies; a later run
gets a new --run name and records what changed in its provenance):
  metrics.json            every number in the report
  audit_pairs.csv         pairs for a human to label (SAME_ENTITY / DIFFERENT_ENTITY / UNSURE)
  audit_pairs.jsonl       the same pairs with the full raw source records
  same_source_lookalikes.csv  look-alike pairs inside ONE source (ER never compares those)
  hours_audit.csv         every opening_hours conflict + every unparsed opening_hours string
  discovery_sample.md     real discovery queries and what came back
  snapshot.json           the frozen WorldSnapshot (content + evidence hash)
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import Base, CanonicalEntity, EntityLink, MatchReview, Place, SourceEntity  # noqa: E402
from app.db.session import create_schema, make_engine  # noqa: E402
from app.shared.geo import Point, distance_km  # noqa: E402
from app.world import coverage, matching, snapshots  # noqa: E402
from app.world.adapters import RawDumpAdapter, SourceDescriptor  # noqa: E402
from app.world.ingest import run_sync  # noqa: E402
from app.world.matching import _address_key, name_similarity  # noqa: E402
from app.world.normalize import normalize  # noqa: E402
from app.world.policies import load  # noqa: E402
from app.world.spatial import GeohashIndex  # noqa: E402

FACT_FIELDS = ("name", "opening_hours", "phone", "website", "address", "coordinates", "temporary_closure",
               "subcategory", "price_range")


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def descriptor(src: dict, cfg: dict) -> SourceDescriptor:
    return SourceDescriptor(
        source_id=src["source_id"], source_type=src["source_type"], source_class=src["source_class"],
        name=src["name"], format=src["format"], license=src["license"],
        attribution_required=src.get("attribution_required", False), attribution_text=src.get("attribution_text"),
        redistribution=src.get("redistribution", "allowed"), default_confidence=src.get("default_confidence", 0.7),
        config={"category_map": src.get("category_map", {}), "country_code": cfg["country_code"],
                "timezone": cfg["timezone"], "region": cfg["region"], "local_language": "sr"})


# --------------------------------------------------------------- coverage
def field_coverage(adapter: RawDumpAdapter, d: SourceDescriptor) -> dict[str, Any]:
    from app.world.adapters import Scope

    recs = [r for page in adapter.sync_full(Scope()) for r in page.records]
    c, langs, issues, unmapped, scripts = Counter(), Counter(), Counter(), Counter(), Counter()
    for rec in recs:
        ent = normalize(rec, d)
        kinds = {k for k, _ in ent.identifiers}
        c["records"] += 1
        c["name"] += bool(ent.fields.get("name"))
        c["coordinates"] += ent.latitude is not None
        c["phone"] += "phone" in kinds
        c["website_domain"] += "domain" in kinds
        c["address_any"] += bool(ent.fields.get("address"))
        c["address_with_house_number"] += bool(_address_key(ent.fields.get("address")))
        c["external_id_cross_source"] += any(k in ("ext:wikidata", "ext:osm") for k in kinds
                                             if not (k == f"ext:{d.source_id}"))
        c["opening_hours_raw"] += _raw_hours(rec, d) is not None
        c["opening_hours_parsed"] += "opening_hours" in ent.fields
        c["category_mapped"] += ent.fields.get("subcategory") not in (None, "unclassified")
        positive = sum(("phone" in kinds, "domain" in kinds, bool(_address_key(ent.fields.get("address"))),
                        any(k in ("ext:wikidata", "ext:osm") for k in kinds)))
        c[f"positive_identity_signals_{min(positive, 3)}{'+' if positive >= 3 else ''}"] += 1
        for alias, lang, _ in ent.aliases:
            langs[lang or "primary"] += 1
            scripts["cyrillic" if any("Ѐ" <= ch <= "ӿ" for ch in alias) else "latin"] += 1
        for i in ent.issues:
            issues[i.split(":")[0]] += 1
            if i.startswith("category not mapped"):
                unmapped[i.split(": ", 1)[1]] += 1
    n = max(c["records"], 1)
    return {"counts": dict(c), "share": {k: round(v / n, 3) for k, v in c.items() if k != "records"},
            "alias_languages": dict(langs.most_common()), "alias_scripts": dict(scripts),
            "issues": dict(issues.most_common()), "unmapped_categories": dict(unmapped.most_common(30))}


def _raw_hours(rec, d) -> str | None:  # noqa: ANN001
    if d.format == "osm.overpass.v1":
        return (rec.payload.get("tags") or {}).get("opening_hours")
    return None


# ------------------------------------------------------------------ audit
def entity_view(session, canonical_id: str) -> dict[str, Any]:  # noqa: ANN001
    ses = session.scalars(select(SourceEntity).join(EntityLink, EntityLink.source_entity_id == SourceEntity.id)
                          .where(EntityLink.canonical_entity_id == canonical_id, EntityLink.active)).all()
    return {"canonical_entity_id": canonical_id, "members": [record_view(se) for se in ses]}


def record_view(se: SourceEntity) -> dict[str, Any]:
    n = se.normalized or {}
    f = n.get("fields", {})
    return {"source": se.source_id, "record_id": se.source_record_id, "name": f.get("name"),
            "aliases": [a[0] for a in n.get("aliases", [])], "address": f.get("address"),
            "lat": se.latitude, "lon": se.longitude, "phone": f.get("phone"), "website": f.get("website"),
            "ids": list(dict.fromkeys(f"{k}={v}" for k, v in n.get("identifiers", []) if k.startswith("ext:"))),
            "category": f.get("subcategory"), "raw": se.raw}


def id_link_direction(evidence: dict[str, Any]) -> str | None:
    """Which data chain an explicit OSM<->Wikidata id link came through.
    NOT independent confirmation: an OSM `wikidata=Q..` tag and Wikidata's
    OSM-id property are often maintained by the same editors, from the
    same knowledge. Reported separately from phone / website / address."""
    shared = evidence.get("shared_ids") or []
    wd = any(x.startswith("ext:wikidata=") for x in shared)      # the OSM side carried wikidata=Q..
    osm = any(x.startswith("ext:osm=") for x in shared)          # the Wikidata side carried an OSM id
    if wd and osm:
        return "both_directions"
    return "osm_wikidata_tag_only" if wd else "wikidata_osm_property_only" if osm else (
        "other_explicit_id" if shared else None)


def independent_support(evidence: dict[str, Any]) -> list[str]:
    """Positive identity evidence that does NOT come from the id-link chain."""
    sig = evidence.get("signals") or {}
    return sorted(k for k in ("phone", "domain", "address") if sig.get(k) == "SUPPORTS_MATCH")


# ------------------------------------------------------- audit sample (pre-registered)
AUDIT_SAMPLE_VERSION = "1"
AUDIT_SEED = 20261007
QUOTA = {"MATCH": 50, "AMBIGUOUS": 50, "NO_MATCH_DIFFICULT": 30}
MIN_PER_STRATUM = 10
SELECTION_ALGORITHM = """\
Pairs = every (record x candidate canonical) decision in matching.PAIR_LOG.
Deterministic order: pairs sorted by (outcome, stratum, source_id, record_id,
stable key of the candidate = its smallest (source_id, record_id) member).
Strata (assigned from the logged evidence, in this precedence):
  MATCH:  explicit_id_only        external_id SUPPORTS, no phone/domain/address SUPPORTS
          explicit_id_plus_independent  external_id SUPPORTS and phone/domain/address SUPPORTS
          contact_based           no external_id; phone or domain SUPPORTS
          address_based           no external_id, no contact; address SUPPORTS
          other_rule              anything else (relocation, event rules, ...)
  AMBIGUOUS: multiple_candidates  the record had >= 2 non-NO_MATCH candidates
          support_and_contradiction  a signal SUPPORTS and another CONTRADICTS
          name_geo_only           no signal SUPPORTS or CONTRADICTS
          other
  NO_MATCH_DIFFICULT (machine criterion, fixed before data): NO_MATCH with
          name_similarity >= 0.6, or distance <= 50 m, or any SUPPORTS signal,
          or any CONTRADICTS signal.
Per-stratum sample size: min(stratum size, max(MIN_PER_STRATUM, ceil(QUOTA x stratum share))).
Each stratum is sampled with random.Random(sha256(f"{seed}:{outcome}:{stratum}")), without replacement.
Census (all pairs, no sampling): unusual MATCH (name_similarity < 0.5 or distance > 150 m) and every pair
with a CONTRADICTS signal.
A pair that falls in several groups is audited once, under the first group in this order:
MATCH strata, AMBIGUOUS strata, NO_MATCH_DIFFICULT, unusual_match_all, contradictory_signals_all.
"""


def _states(p: dict) -> dict[str, str]:
    return p["evidence"].get("signals") or {}


def stratum(p: dict, multi: set[tuple[str, str]]) -> str | None:
    sig = _states(p)
    supports = {k for k, v in sig.items() if v == "SUPPORTS_MATCH"}
    contradicts = {k for k, v in sig.items() if v == "CONTRADICTS_MATCH"}
    if p["outcome"] == "MATCH":
        if "external_id" in supports:
            return "explicit_id_plus_independent" if supports - {"external_id"} else "explicit_id_only"
        if supports & {"phone", "domain"}:
            return "contact_based"
        if "address" in supports:
            return "address_based"
        return "other_rule"
    if p["outcome"] == "AMBIGUOUS":
        if (p["source_id"], p["record_id"]) in multi:
            return "multiple_candidates"
        if supports and contradicts:
            return "support_and_contradiction"
        if not supports and not contradicts:
            return "name_geo_only"
        return "other"
    e = p["evidence"]
    if e.get("name_similarity", 0) >= 0.6 or (e.get("distance_m") if e.get("distance_m") is not None else 1e9) <= 50 \
            or supports or contradicts:
        return "difficult"
    return None


def stratified_sample(log: list[dict], stable_key, seed: int = AUDIT_SEED) -> tuple[dict, dict]:  # noqa: ANN001
    import hashlib
    import math

    live = Counter((p["source_id"], p["record_id"]) for p in log if p["outcome"] != "NO_MATCH")
    multi = {k for k, n in live.items() if n >= 2}
    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in log:
        st = stratum(p, multi)
        if st is not None:
            strata[("NO_MATCH_DIFFICULT" if p["outcome"] == "NO_MATCH" else p["outcome"], st)].append(p)
    groups, manifest = {}, {}
    for outcome in ("MATCH", "AMBIGUOUS", "NO_MATCH_DIFFICULT"):
        total = sum(len(v) for (o, _), v in strata.items() if o == outcome)
        for (o, st), pairs in sorted(strata.items()):
            if o != outcome:
                continue
            pairs.sort(key=lambda p: (p["source_id"], p["record_id"], stable_key(p["canonical_entity_id"])))
            k = min(len(pairs), max(MIN_PER_STRATUM, math.ceil(QUOTA[outcome] * len(pairs) / max(total, 1))))
            rng = random.Random(hashlib.sha256(f"{seed}:{outcome}:{st}".encode()).hexdigest())
            groups[f"{outcome.lower()}:{st}"] = rng.sample(pairs, k)
            manifest[f"{outcome.lower()}:{st}"] = {"population": len(pairs), "sampled": k}
    ev = lambda p: p["evidence"]  # noqa: E731
    order = lambda ps: sorted(ps, key=lambda p: (p["source_id"], p["record_id"],  # noqa: E731
                                                 stable_key(p["canonical_entity_id"])))
    groups["unusual_match_all"] = order([p for p in log if p["outcome"] == "MATCH" and (
        ev(p).get("name_similarity", 1) < 0.5 or (ev(p).get("distance_m") or 0) > 150)])
    groups["contradictory_signals_all"] = order([p for p in log if "CONTRADICTS_MATCH" in _states(p).values()])
    for g in ("unusual_match_all", "contradictory_signals_all"):
        manifest[g] = {"population": len(groups[g]), "sampled": len(groups[g])}
    return groups, manifest


def audit(session, log: list[dict], out: Path, header: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
    ev = lambda p: p["evidence"]  # noqa: E731
    keys: dict[str, str] = {}

    def stable_key(canonical_id: str) -> str:
        if canonical_id not in keys:
            members = session.execute(select(SourceEntity.source_id, SourceEntity.source_record_id)
                                      .join(EntityLink, EntityLink.source_entity_id == SourceEntity.id)
                                      .where(EntityLink.canonical_entity_id == canonical_id)).all()
            keys[canonical_id] = min((f"{a}:{b}" for a, b in members), default=canonical_id)
        return keys[canonical_id]

    groups, manifest = stratified_sample(log, stable_key)
    rows, seen = [], set()
    for group, pairs in groups.items():
        for p in pairs:
            key = (p["source_id"], p["record_id"], p["canonical_entity_id"])
            if key in seen:
                continue
            seen.add(key)
            se = session.scalar(select(SourceEntity).where(SourceEntity.source_id == p["source_id"],
                                                           SourceEntity.source_record_id == p["record_id"]))
            a = record_view(se)
            b = entity_view(session, p["canonical_entity_id"])
            b["members"] = [m for m in b["members"] if (m["source"], m["record_id"]) != (a["source"], a["record_id"])]
            rows.append({"pair_id": f"P{len(rows) + 1:04d}", "group": group,
                         "stratum": group.split(":", 1)[1] if ":" in group else group, "decision": p["outcome"],
                         "rule": ev(p).get("rule"), "score": p["score"], "evidence": ev(p),
                         "candidate_reason": p.get("candidate_reason", []),
                         "id_link_direction": id_link_direction(ev(p)),
                         "independent_support": independent_support(ev(p)), "a": a, "b": b})
    with (out / "audit_pairs.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with (out / "audit_pairs.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pair_id", "group", "decision", "rule", "candidate_reason", "id_link_direction",
                    "independent_support", "distance_m", "name_similarity", "signals",
                    "a_source", "a_id", "a_name", "a_address", "a_phone", "a_website", "a_ids", "a_category",
                    "b_records", "b_names", "b_addresses", "b_phones", "b_websites", "b_ids", "b_categories",
                    "label", "notes"])
        for r in rows:
            e, a, ms = r["evidence"], r["a"], r["b"]["members"]
            j = lambda k: " | ".join(str(m[k]) for m in ms if m[k])  # noqa: E731
            w.writerow([r["pair_id"], r["group"], r["decision"], r["rule"], " ".join(r["candidate_reason"]),
                        r["id_link_direction"] or "", " ".join(r["independent_support"]), e.get("distance_m"),
                        e.get("name_similarity"), json.dumps(e.get("signals")), a["source"], a["record_id"],
                        a["name"], a["address"], a["phone"], a["website"], " ".join(a["ids"]), a["category"],
                        " | ".join(f"{m['source']}:{m['record_id']}" for m in ms), j("name"), j("address"),
                        j("phone"), j("website"), " | ".join(" ".join(m["ids"]) for m in ms), j("category"), "", ""])
    sample = {"audit_sample_version": AUDIT_SAMPLE_VERSION, "selection_algorithm": SELECTION_ALGORITHM,
              "random_seed": AUDIT_SEED, "quota": QUOTA, "min_per_stratum": MIN_PER_STRATUM, **header,
              "strata": manifest, "rows_written": len(rows),
              "pairs": [{"pair_id": r["pair_id"], "group": r["group"], "a": f"{r['a']['source']}:{r['a']['record_id']}",
                         "b": stable_key(r["b"]["canonical_entity_id"])} for r in rows]}
    (out / "audit_sample.json").write_text(json.dumps(sample, indent=1, ensure_ascii=False))
    return {"strata": manifest, "rows_written": len(rows)}


def same_source_lookalikes(session, out: Path) -> int:  # noqa: ANN001
    """ER never compares two records of ONE source (by design). Duplicate map
    listings inside a source are therefore invisible to it - list them."""
    found = []
    by_source = defaultdict(list)
    for se in session.scalars(select(SourceEntity).where(SourceEntity.latitude.is_not(None))):
        by_source[se.source_id].append(se)
    for source, ses in by_source.items():
        cells = defaultdict(list)
        for se in ses:
            cells[(round(se.latitude, 3), round(se.longitude, 3))].append(se)
        for se in ses:
            for dl in (-1, 0, 1):
                for dn in (-1, 0, 1):
                    for other in cells[(round(se.latitude, 3) + dl / 1000, round(se.longitude, 3) + dn / 1000)]:
                        if other.id <= se.id:
                            continue
                        a, b = (se.normalized or {}).get("fields", {}), (other.normalized or {}).get("fields", {})
                        if not a.get("name") or not b.get("name"):
                            continue
                        d = distance_km(Point(se.latitude, se.longitude), Point(other.latitude, other.longitude))
                        s, _ = name_similarity(a["name"], b["name"])
                        if d <= 0.1 and s >= 0.85:
                            found.append([source, se.source_record_id, a["name"], other.source_record_id, b["name"],
                                          round(d * 1000), round(s, 3), a.get("subcategory"), b.get("subcategory"),
                                          "", ""])
    with (out / "same_source_lookalikes.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "a_id", "a_name", "b_id", "b_name", "distance_m", "name_similarity", "a_category",
                    "b_category", "label", "notes"])
        w.writerows(found)
    return len(found)


# ------------------------------------------------------------------ facts
def fact_conflicts(session) -> dict[str, Any]:  # noqa: ANN001
    stats: dict[str, Counter] = {f: Counter() for f in FACT_FIELDS}
    winners: dict[str, Counter] = {f: Counter() for f in FACT_FIELDS}
    multi = 0
    for place in session.scalars(select(Place).where(Place.active, Place.canonical_entity_id.is_not(None))):
        res = place.resolution or {}
        sources = {s for r in res.values() if isinstance(r, dict) for s in (r.get("supporting") or [])}
        if len(sources) < 2:
            continue
        multi += 1
        for f in FACT_FIELDS:
            r = res.get(f)
            if not r:
                stats[f]["absent_everywhere"] += 1
                continue
            conflicting = len(r.get("conflicting") or [])
            agreeing = len(r.get("supporting") or [])
            if r["state"] in ("needs_verification", "conflicted"):
                stats[f]["conflict_unresolved"] += 1
            elif r["state"] == "contested":
                stats[f]["contested"] += 1
            elif agreeing >= 2:
                stats[f]["agree"] += 1
            elif conflicting == 0:
                stats[f]["single_source"] += 1
            if r.get("source"):
                winners[f][r["source"]] += 1
    out = {"multi_source_places": multi}
    for f in FACT_FIELDS:
        n = sum(stats[f].values()) or 1
        out[f] = {**dict(stats[f]), "rates": {k: round(v / n, 3) for k, v in stats[f].items()},
                  "winner_sources": dict(winners[f])}
    return out


def hours_audit(session, out: Path) -> dict[str, int]:  # noqa: ANN001
    rows, c = [], Counter()
    for se in session.scalars(select(SourceEntity)):
        raw_hours = ((se.raw or {}).get("tags") or {}).get("opening_hours")
        n = se.normalized or {}
        if raw_hours and "opening_hours" not in n.get("fields", {}):
            c["unparsed"] += 1
            rows.append(["unparsed", se.source_id, se.source_record_id, (n.get("fields") or {}).get("name"),
                         raw_hours, "", "", ""])
        elif raw_hours:
            c["parsed"] += 1
    for place in session.scalars(select(Place).where(Place.active)):
        r = (place.resolution or {}).get("opening_hours") or {}
        if r.get("conflicting"):
            c[f"conflict_{r['state']}"] += 1
            rows.append([f"conflict:{r['state']}", "", place.canonical_entity_id, place.name,
                         json.dumps(r.get("value"), ensure_ascii=False),
                         json.dumps(r.get("conflicting"), ensure_ascii=False), "", ""])
    with (out / "hours_audit.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "source", "id", "name", "value_or_raw", "conflicting", "cause", "notes"])
        w.writerows(rows)
    return dict(c)


# -------------------------------------------------------------- discovery
QUERIES = [("Find a restaurant in Kotor open tonight", "kotor"), ("Where can I get cocktails in Budva?", "budva"),
           ("Nearest pharmacy", "kotor"), ("Something interesting near the Old Town", "kotor"),
           ("Where can we eat late?", "budva"), ("Is there an ATM nearby?", "budva"),
           ("A museum in Kotor", "kotor"), ("Find a beach in Budva", "budva")]
ANCHORS = {"kotor": (42.4247, 18.7712), "budva": (42.2780, 18.8380)}     # old-town gates


def discovery_sample(session, out: Path, now: datetime, region: str) -> list[dict]:  # noqa: ANN001
    from app.discovery.engine import run
    from app.discovery.nlu import parse_discovery
    from app.discovery.render import candidate_line
    from app.places.hours import local_at

    lines, results = ["# Discovery sample (real data, raw-first)", ""], []
    for text, anchor in QUERIES:
        local = local_at(now, "Europe/Podgorica", "Europe/Podgorica")
        req = parse_discovery(text, local, require_cue=False)
        if req is None:
            lines += [f"## {text}", "", "(not understood as a discovery request)", ""]
            continue
        q = req.query
        q.region, q.near, q.limit = region, Point(*ANCHORS[anchor]), 5
        res = run(session, q, now, "Europe/Podgorica")
        rendered = [candidate_line(i + 1, c, "en", anchor=f"{anchor} old town") for i, c in enumerate(res.candidates)]
        results.append({"query": text, "retrieved": res.retrieved, "rejected": dict(res.rejected),
                        "results": [c.place.name for c in res.candidates]})
        lines += [f"## {text}", "", f"retrieved {res.retrieved}, rejected {dict(res.rejected)}", ""] + \
            [f"    {r}" for r in rendered] + ["", "Inspect: correct candidates? open state? distance? freshness "
                                              "disclosed? duplicates? contested facts hedged?", ""]
    (out / "discovery_sample.md").write_text("\n".join(lines))
    return results


# ------------------------------------------------------------- provenance
def provenance(pilot: Path, cfg: dict) -> dict[str, Any]:
    """What exactly produced this report: dump hashes, the config (category
    maps) hash and the code version - so a later report can be compared
    to this one knowing what changed."""
    import hashlib
    import subprocess

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    root = Path(__file__).resolve().parents[1]
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=root).stdout.strip()  # noqa: E731
    return {"dumps": {src["source_id"]: sha(pilot / src["dump"]) for src in cfg["sources"]},
            "pilot_yaml_sha256": sha(pilot / "pilot.yaml"),
            "policies_sha256": sha(root / "data" / "world" / "policies.yaml"),
            "code_commit": git("rev-parse", "HEAD") or None,
            "code_dirty": bool(git("status", "--porcelain", "--", "app", "tools", "data/world"))}


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", default="data/pilot/kotor_budva")
    ap.add_argument("--db", default="sqlite://")
    ap.add_argument("--now", help="ISO time the pilot is evaluated at (default: the newest dump's fetch time)")
    ap.add_argument("--run", default="raw_v1",
                    help="report name; a report is WRITE-ONCE (the first raw report is frozen before any change)")
    args = ap.parse_args()
    pilot = Path(args.pilot)
    cfg = yaml.safe_load((pilot / "pilot.yaml").read_text())
    out = pilot / "out" / args.run
    if (out / "metrics.json").exists():
        print(f"{out} already holds a frozen report - pick a new --run name (reports are never overwritten)")
        return 3
    out.mkdir(parents=True, exist_ok=True)
    dumps = {}
    for src in cfg["sources"]:
        path = pilot / src["dump"]
        if not path.exists():
            print(f"missing dump {path} - run tools/pilot_fetch.py first (needs network access to the source)")
            return 2
        dumps[src["source_id"]] = json.loads(path.read_text())
    now = datetime.fromisoformat(args.now) if args.now else max(
        datetime.fromisoformat(d["fetched_at"]) for d in dumps.values())
    engine = make_engine(args.db)
    if not args.db.startswith("sqlite"):
        with engine.begin() as conn:
            Base.metadata.drop_all(conn)
    create_schema(engine)
    from sqlalchemy.orm import sessionmaker

    s = sessionmaker(engine)()
    policies = load()
    for name, box in cfg["scope"].items():
        coverage.upsert_area(s, code=f"{cfg['region']}:{name}", kind="locality", name=name.title(),
                             country_code=cfg["country_code"], timezone=cfg["timezone"], bbox=tuple(box))
    metrics: dict[str, Any] = {"run": args.run, "now": now.isoformat(), "provenance": provenance(pilot, cfg),
                               "sources": {}, "ingest": {}}
    matching.PAIR_LOG, matching.CANDIDATE_SIZES = [], []
    for src in cfg["sources"]:
        d = descriptor(src, cfg)
        adapter = RawDumpAdapter(d, dumps[src["source_id"]])
        metrics["sources"][d.source_id] = {"license": d.license, "attribution": d.attribution_text,
                                           "fetched_at": dumps[d.source_id]["fetched_at"],
                                           "parts": [{"box": p["box"], "sha256": p["sha256"]}
                                                     for p in dumps[d.source_id].get("parts", [])],
                                           "field_coverage": field_coverage(adapter, d)}
        before = dict(matching.STATS)
        t0 = time.perf_counter()
        rep = run_sync(s, adapter, now=now, policies=policies)
        s.commit()
        secs = time.perf_counter() - t0
        metrics["ingest"][d.source_id] = {
            "records": rep.seen, "seconds": round(secs, 1), "records_per_s": round(rep.seen / max(secs, 1e-9), 1),
            "status": rep.status, "decisions": {k: matching.STATS[k] - before.get(k, 0) for k in matching.STATS}}
    sizes = matching.CANDIDATE_SIZES
    log = matching.PAIR_LOG
    matching.PAIR_LOG = matching.CANDIDATE_SIZES = None
    metrics["candidates_per_record"] = {"p50": pct(sizes, 0.5), "p95": pct(sizes, 0.95), "p99": pct(sizes, 0.99),
                                        "max": max(sizes, default=0),
                                        "mean": round(statistics.mean(sizes), 2) if sizes else 0}
    metrics["pairs"] = {"total": len(log), "by_outcome": dict(Counter(p["outcome"] for p in log)),
                        "by_rule": dict(Counter(p["evidence"].get("rule", "-") for p in log).most_common(25)),
                        "signal_states": {sig: dict(Counter((p["evidence"].get("signals") or {}).get(sig, "-")
                                                            for p in log))
                                          for sig in ("phone", "domain", "address", "external_id")},
                        "candidate_reasons": dict(Counter(" + ".join(p.get("candidate_reason") or ["-"])
                                                          for p in log)),
                        "matches": {
                            "id_link_direction": dict(Counter(id_link_direction(p["evidence"]) or "none"
                                                              for p in log if p["outcome"] == "MATCH")),
                            "with_independent_support": sum(1 for p in log if p["outcome"] == "MATCH"
                                                            and independent_support(p["evidence"])),
                            "only_by_id_link_chain": sum(1 for p in log if p["outcome"] == "MATCH"
                                                         and id_link_direction(p["evidence"])
                                                         and not independent_support(p["evidence"]))}}
    metrics["world"] = {"source_entities": s.query(SourceEntity).count(),
                        "canonical_entities": s.query(CanonicalEntity).filter(CanonicalEntity.active).count(),
                        "multi_source_canonicals": sum(
                            1 for c in s.scalars(select(CanonicalEntity).where(CanonicalEntity.active))
                            if s.query(EntityLink).filter(EntityLink.canonical_entity_id == c.id,
                                                          EntityLink.active).count() > 1),
                        "reviews_open": s.query(MatchReview).filter(MatchReview.status == "open").count()}
    rng = random.Random(6)
    metrics["audit"] = audit(s, log, out, {"source_dump_hashes": metrics["provenance"]["dumps"],
                                           "code_commit": metrics["provenance"]["code_commit"],
                                           "code_dirty": metrics["provenance"]["code_dirty"]})
    metrics["same_source_lookalikes"] = same_source_lookalikes(s, out)
    metrics["facts"] = fact_conflicts(s)
    metrics["opening_hours"] = hours_audit(s, out)
    metrics["discovery"] = discovery_sample(s, out, now, cfg["region"])
    idx, t_r, t_n = GeohashIndex(), [], []
    for i in range(200):
        a = ANCHORS["kotor" if i % 2 else "budva"]
        c = Point(a[0] + rng.uniform(-0.01, 0.01), a[1] + rng.uniform(-0.01, 0.01))
        t0 = time.perf_counter()
        idx.within_radius(s, c, 1.0)
        t_r.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        idx.nearest(s, c, 5)
        t_n.append((time.perf_counter() - t0) * 1000)
    metrics["spatial_ms"] = {"radius_1km_p50": round(pct(t_r, 0.5), 2), "radius_1km_p95": round(pct(t_r, 0.95), 2),
                             "nearest_5_p50": round(pct(t_n, 0.5), 2), "nearest_5_p95": round(pct(t_n, 0.95), 2)}
    snap = snapshots.create(s, "kotor-budva-pilot-raw", now)
    s.commit()
    (out / "snapshot.json").write_text(json.dumps({"name": snap.name, "content_hash": snap.content_hash,
                                                    "created_at": now.isoformat(), "content": snap.content}))
    metrics["snapshot"] = {"name": snap.name, "content_hash": snap.content_hash}
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: metrics[k] for k in ("ingest", "candidates_per_record", "world", "audit", "snapshot")},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
