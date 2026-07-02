from __future__ import annotations

import json

from e2e.experiments.agent_ab.tools import advisory_check_sham as sham
from e2e.experiments.agent_ab.tools.advisory_contract import OUTPUT_KEYS

_FORBIDDEN = ("graph", "fan-in", "percentile", "pebra", "codegraph")


def test_sham_is_deterministic_regardless_of_input():
    a = sham.advise({"target_file": "x.cs", "change_summary": "delete a widely-used class"})
    b = sham.advise({"target_file": "y.cs", "change_summary": "trivial rename"})
    assert a == b


def test_sham_has_no_decision_and_unknown_risk():
    out = sham.advise({})
    assert out["recommended_decision"] is None
    assert out["risk_level"] == "unknown"


def test_sham_output_shape_matches_contract():
    out = sham.advise({})
    assert set(out) == set(OUTPUT_KEYS)


def test_sham_never_leaks_engine_vocabulary():
    blob = json.dumps(sham.advise({"change_summary": "anything"})).lower()
    assert not any(word in blob for word in _FORBIDDEN)
