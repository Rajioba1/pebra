"""Guard the invariant the seeded-learning proof rests on.

The headline test compares a BASELINE assess (request_first_edit, no snapshot) to a LEARNED reassess
(request_second_edit, active snapshot) and asserts the RAU dropped + the decision shifted. That
cross-request comparison is only sound if the two requests are SCORING-IDENTICAL — they must differ
ONLY in identity (task / action id / label), never in evidence or thresholds. Otherwise a future edit to
the second fixture could make the comparison pass (or fail) for reasons unrelated to learning.

The seeded-learning host explicitly requests ``applied_snapshot_provenance``, so the headline test
can assert directly that the future assess consumed the promoted snapshot without exposing that
host-only evidence to ordinary model-facing CLI or MCP consumers. This fixture guard still matters:
it keeps the baseline-vs-learned RAU/decision comparison tied to learning rather than fixture drift.
"""

from __future__ import annotations

import json
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_first_and_second_requests_are_scoring_equivalent():
    first = _load("request_first_edit.json")
    second = _load("request_second_edit.json")
    # identical in everything that feeds scoring ...
    assert first["evidence"] == second["evidence"]
    assert first["thresholds"] == second["thresholds"]
    # ... but a DISTINCT future proposal in identity (not the same staged diff)
    assert first["task"] != second["task"]
    assert first["candidate_actions"][0]["id"] != second["candidate_actions"][0]["id"]
