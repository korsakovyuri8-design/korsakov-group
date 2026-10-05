"""The product evaluation harness as part of the test suite.

* every scenario file parses and names are unique;
* every scenario runs without crashing the harness;
* GATE scenarios (product invariants: authority, safety, memory isolation,
  capability honesty) must pass. Non-gate scenarios are a benchmark and
  are reported by `python -m evals`, not enforced here.
"""

import pytest

from evals.harness import load_scenarios, run_scenario

SCENARIOS = load_scenarios()


def test_scenario_suite_shape():
    assert 40 <= len(SCENARIOS)
    assert {s.category for s in SCENARIOS} == {
        "grounding", "actions", "authority", "handoff", "safety", "memory", "conversation", "languages",
        "transactions", "local", "marketplace"}


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.gate], ids=lambda s: s.name)
def test_gate_scenario(scenario):
    result = run_scenario(scenario)
    assert result.error is None, result.error
    assert result.failures == []


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if not s.gate], ids=lambda s: s.name)
def test_benchmark_scenario_runs(scenario):
    result = run_scenario(scenario)
    assert result.error is None, result.error  # may fail expectations, must not crash
