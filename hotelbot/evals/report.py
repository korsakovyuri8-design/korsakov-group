"""Compact console summary + Markdown/JSON reports for evaluation runs."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from evals.harness import ScenarioResult
from evals.schema import CATEGORIES


def summary(results: list[ScenarioResult]) -> str:
    passed = sum(r.passed for r in results)
    gates = [r for r in results if r.scenario.gate]
    lines = [
        f"TOTAL {len(results)}",
        f"PASS  {passed}",
        f"FAIL  {len(results) - passed}",
        f"GATES {sum(r.passed for r in gates)}/{len(gates)} (product invariants)",
        "",
        "by category:",
    ]
    by_cat: dict[str, list[ScenarioResult]] = defaultdict(list)
    for r in results:
        by_cat[r.scenario.category].append(r)
    for cat in CATEGORIES:
        rs = by_cat.get(cat, [])
        if rs:
            lines.append(f"  {cat:<13} {sum(r.passed for r in rs):>2}/{len(rs)}")
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["", "FAIL:"]
        for r in failed:
            gate = " [GATE]" if r.scenario.gate else ""
            lines.append(f"  {r.scenario.category}/{r.scenario.name}{gate}")
            for f in ([r.error] if r.error else []) + r.failures:
                lines.append(f"      - {f}")
    return "\n".join(lines)


def to_json(results: list[ScenarioResult]) -> str:
    return json.dumps([
        {"name": r.scenario.name, "category": r.scenario.category, "gate": r.scenario.gate, "passed": r.passed,
         "failures": r.failures, "error": r.error, "seconds": round(r.seconds, 3),
         "transcript": [{"who": t.who, "text": t.text, **t.meta} for t in r.transcript]}
        for r in results
    ], ensure_ascii=False, indent=2)


def to_markdown(results: list[ScenarioResult]) -> str:
    passed = sum(r.passed for r in results)
    out = [
        "# HOTELBOT evaluation report",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by `python -m evals`. "
        "Deterministic: no LLM unless a scenario scripts one; synthetic property packs.",
        "",
        f"**TOTAL {len(results)} · PASS {passed} · FAIL {len(results) - passed}**",
        "",
        "| Category | Pass |",
        "|---|---|",
    ]
    by_cat: dict[str, list[ScenarioResult]] = defaultdict(list)
    for r in results:
        by_cat[r.scenario.category].append(r)
    for cat in CATEGORIES:
        rs = by_cat.get(cat, [])
        if rs:
            out.append(f"| {cat} | {sum(r.passed for r in rs)}/{len(rs)} |")
    out += ["", "## Scenarios", "", "| | Scenario | Category | Gate |", "|---|---|---|---|"]
    for r in results:
        out.append(f"| {'✅' if r.passed else '❌'} | `{r.scenario.name}` | {r.scenario.category} | "
                   f"{'yes' if r.scenario.gate else ''} |")
    failed = [r for r in results if not r.passed]
    if failed:
        out += ["", "## Failures", ""]
        for r in failed:
            out += [f"### `{r.scenario.name}` ({r.scenario.category})", "", r.scenario.description, ""]
            for f in ([r.error] if r.error else []) + r.failures:
                out.append(f"- {f}")
            out += ["", "Transcript:", "", "```"]
            for t in r.transcript:
                meta = {k: v for k, v in t.meta.items() if v not in (None, [], {})}
                out.append(f"{t.who:>5}: {t.text}")
                if t.who == "bot" and meta:
                    out.append(f"       {meta}")
            out += ["```", ""]
    return "\n".join(out) + "\n"
