"""Guard the invariant the seeded-learning proof rests on.

The headline test compares a BASELINE assess (request_first_edit, no snapshot) to a LEARNED reassess
(request_second_edit, active snapshot) and asserts the RAU dropped + the decision shifted. That
cross-request comparison is only sound if the two requests are SCORING-IDENTICAL — they must differ
ONLY in identity (task / action id / label), never in evidence or thresholds. Otherwise a future edit to
the second fixture could make the comparison pass (or fail) for reasons unrelated to learning.

We can't instead assert that the snapshot was applied: ``applied_snapshot_provenance`` is internal and is
NOT emitted in the ``assess --json`` payload (composition.assess_payload), so at the agent/CLI boundary
the learning proof is necessarily indirect (promotion fired + risk_snapshots>=1 + RAU drop on a
scoring-equivalent request). This guard keeps that indirect proof honest.
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
