"""Iteration 6: score the frozen ER decisions against HUMAN labels.

    python tools/pilot_labels.py [--pilot data/pilot/kotor_budva] [--run raw_v1] [--labels FILE]

Labels live in their own file (<pilot>/labels_<run>.csv: pair_id,label,notes
with label in SAME_ENTITY | DIFFERENT_ENTITY | UNSURE), never in the
algorithm's output. A label can be typed straight into
out/<run>/audit_pairs.csv's `label` column instead; this tool reads either.

UNSURE is its own category: neither an algorithm error nor a success. It is
excluded from precision, false merges and false splits, and counted apart.

Reported on the reviewed sample only (it is a sample, not the population):
  false merges   MATCH labelled DIFFERENT_ENTITY
  false splits   NO_MATCH labelled SAME_ENTITY
  precision      SAME among labelled MATCH
  ambiguous      how AMBIGUOUS pairs split under human review (usefulness)
Sample groups are reported separately: the 50 random MATCH pairs estimate
precision; the targeted groups (unusual, contradictory, difficult) do not.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

LABELS = ("SAME_ENTITY", "DIFFERENT_ENTITY", "UNSURE")


def load_labels(pilot: Path, run: Path, labels: Path | None) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for path in [p for p in (labels or pilot / f"labels_{run.name}.csv", run / "audit_pairs.csv") if p.exists()]:
        with path.open() as fh:
            for row in csv.DictReader(fh):
                label = (row.get("label") or "").strip().upper()
                if label:
                    if label not in LABELS:
                        raise SystemExit(f"{path}: pair {row['pair_id']}: unknown label {label!r}")
                    out.setdefault(row["pair_id"], (label, row.get("notes") or ""))
    return out


def score(pairs: list[dict], labels: dict[str, tuple[str, str]]) -> dict:
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
            false_merges.append({"pair_id": p["pair_id"], "rule": p["rule"], "notes": notes})
        if p["decision"] == "NO_MATCH" and label == "SAME_ENTITY":
            false_splits.append({"pair_id": p["pair_id"], "rule": p["rule"], "notes": notes})
    rs = by_group.get("match_sample", Counter())
    labelled_match = rs["MATCH->SAME_ENTITY"] + rs["MATCH->DIFFERENT_ENTITY"]
    amb = Counter({k.split("->")[1]: v for k, v in overall.items() if k.startswith("AMBIGUOUS->")})
    unsure = {g: sum(v for k, v in c.items() if k.endswith("->UNSURE")) for g, c in by_group.items()}
    return {"labelled": sum(overall.values()), "of_pairs": len(pairs),
            "unsure_by_group": unsure, "unsure_total": sum(unsure.values()),
            "precision_on_random_match_sample": round(rs["MATCH->SAME_ENTITY"] / labelled_match, 4)
            if labelled_match else None,
            "false_merges": false_merges, "false_splits": false_splits,
            "ambiguous_under_review": dict(amb),
            "by_group": {g: dict(c) for g, c in by_group.items()}, "overall": dict(overall)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", default="data/pilot/kotor_budva")
    ap.add_argument("--labels")
    ap.add_argument("--run", default="raw_v1")
    args = ap.parse_args()
    pilot = Path(args.pilot)
    run = pilot / "out" / args.run
    pairs = [json.loads(line) for line in (run / "audit_pairs.jsonl").open()]
    result = score(pairs, load_labels(pilot, run, Path(args.labels) if args.labels else None))
    (run / "label_metrics.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
