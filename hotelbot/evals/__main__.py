"""Run the product evaluation.

    python -m evals                         # all scenarios, console summary
    python -m evals --category authority    # one category
    python -m evals --name false_premise_spa
    python -m evals --markdown docs/EVAL_REPORT.md --json eval.json

Exit code is 1 if any *gate* scenario (product invariant) fails, so CI can
block on invariants while benchmark scenarios are tracked as a pass rate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.harness import load_scenarios, run_all
from evals.report import summary, to_json, to_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="HOTELBOT product evaluation")
    parser.add_argument("--category")
    parser.add_argument("--name")
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    scenarios = [s for s in load_scenarios()
                 if (not args.category or s.category == args.category) and (not args.name or s.name == args.name)]
    results = run_all(scenarios)
    print(summary(results))
    if args.markdown:
        args.markdown.write_text(to_markdown(results), encoding="utf-8")
    if args.json:
        args.json.write_text(to_json(results), encoding="utf-8")
    return 1 if any(r.scenario.gate and not r.passed for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
