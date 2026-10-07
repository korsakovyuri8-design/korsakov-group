"""Iteration 6: score the frozen ER decisions against HUMAN labels.

    python tools/pilot_labels.py [--pilot data/pilot/kotor_budva] [--run raw_v1] [--labels FILE]

BLIND LABELLING (hard rule): labels come from out/<run>/audit_blind.csv
(columns human_label, human_note) or from <pilot>/labels_<run>.csv
(pair_id,label,notes). Both show only what the sources say. The technical
audit (decision, stratum, rule, evidence) is NEVER a label source; it is
joined by pair_id only here, after the labels exist.

LABELS FREEZE: the labels file's sha256 is recorded in the result, and a
result computed from one labels file is never overwritten by another (a new
file name label_metrics_<hash8>.json is written instead).

Labels: SAME_ENTITY | DIFFERENT_ENTITY | UNSURE. UNSURE is its own category:
neither an algorithm error nor a success; excluded from precision, false
merges and false splits; counted apart.

Per stratum (pre-registered in out/<run>/audit_sample.json): population_size,
n_labeled, n_same, n_different, n_unsure, and for MATCH strata the precision
(n_same / (n_same + n_different)). Overall precision is weighted by the
strata's POPULATION sizes, not by how many rows were labelled. Census groups
(unusual, contradictory) and difficult NO_MATCH find errors; they do not
estimate rates.
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

LABELS = ("SAME_ENTITY", "DIFFERENT_ENTITY", "UNSURE")


def load_labels(pilot: Path, run: Path, labels: Path | None) -> tuple[dict[str, tuple[str, str]], Path | None]:
    """From the blind view or a separate labels file - never the technical audit."""
    candidates = [labels] if labels else [pilot / f"labels_{run.name}.csv", run / "audit_blind.csv"]
    for path in candidates:
        if path is None or not path.exists():
            continue
        if path.name.startswith("audit_pairs"):
            raise SystemExit("the technical audit shows the algorithm's decision - label the blind view instead")
        out: dict[str, tuple[str, str]] = {}
        with path.open() as fh:
            for row in csv.DictReader(fh):
                label = (row.get("human_label") or row.get("label") or "").strip().upper()
                if label:
                    if label not in LABELS:
                        raise SystemExit(f"{path}: pair {row['pair_id']}: unknown label {label!r}")
                    out[row["pair_id"]] = (label, row.get("human_note") or row.get("notes") or "")
        if out:
            return out, path
    return {}, None


def score(pairs: list[dict], labels: dict[str, tuple[str, str]], strata: dict[str, dict]) -> dict:
    by_group: dict[str, Counter] = defaultdict(Counter)
    overall = Counter()
    false_merges, false_splits = [], []
    for p in pairs:
        if p["pair_id"] not in labels:
            continue
        label, notes = labels[p["pair_id"]]
        key = f"{p['decision']}->{label}"
        by_group[p["group"]][key] += 1
        overall[key] += 1
        if p["decision"] == "MATCH" and label == "DIFFERENT_ENTITY":
            false_merges.append({"pair_id": p["pair_id"], "group": p["group"], "rule": p["rule"], "notes": notes})
        if p["decision"] == "NO_MATCH" and label == "SAME_ENTITY":
            false_splits.append({"pair_id": p["pair_id"], "group": p["group"], "rule": p["rule"], "notes": notes})
    per_stratum, weighted, weight = {}, 0.0, 0
    for g in sorted(set(strata) | set(by_group)):
        c = by_group.get(g, Counter())
        n_same = sum(v for k, v in c.items() if k.endswith("->SAME_ENTITY"))
        n_diff = sum(v for k, v in c.items() if k.endswith("->DIFFERENT_ENTITY"))
        n_unsure = sum(v for k, v in c.items() if k.endswith("->UNSURE"))
        row = {"population_size": (strata.get(g) or {}).get("population"),
               "n_sampled": (strata.get(g) or {}).get("sampled"),
               "n_labeled": n_same + n_diff + n_unsure, "n_same": n_same, "n_different": n_diff,
               "n_unsure": n_unsure}
        if g.startswith("match:") and n_same + n_diff:
            row["precision"] = round(n_same / (n_same + n_diff), 4)
            weighted += row["precision"] * (row["population_size"] or 0)
            weight += row["population_size"] or 0
        per_stratum[g] = row
    return {"labelled": sum(overall.values()), "of_pairs": len(pairs),
            "unsure_total": sum(r["n_unsure"] for r in per_stratum.values()),
            "strata": per_stratum,
            "precision_population_weighted": round(weighted / weight, 4) if weight else None,
            "false_merges": false_merges, "false_splits": false_splits, "overall": dict(overall)}


def main() -> int:
    import hashlib

    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", default="data/pilot/kotor_budva")
    ap.add_argument("--labels")
    ap.add_argument("--run", default="raw_v1")
    args = ap.parse_args()
    pilot = Path(args.pilot)
    run = pilot / "out" / args.run
    pairs = [json.loads(line) for line in (run / "audit_pairs.jsonl").open()]
    sample = json.loads((run / "audit_sample.json").read_text())
    labels, source = load_labels(pilot, run, Path(args.labels) if args.labels else None)
    if source is None:
        raise SystemExit("no labels yet: fill human_label in out/<run>/audit_blind.csv or write labels_<run>.csv")
    result = score(pairs, labels, sample["strata"])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result |= {"audit_sample_version": sample["audit_sample_version"], "labels_file": source.name,
               "labels_sha256": digest}
    target = run / "label_metrics.json"
    if target.exists() and json.loads(target.read_text()).get("labels_sha256") != digest:
        target = run / f"label_metrics_{digest[:8]}.json"          # frozen results are never overwritten
    target.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
