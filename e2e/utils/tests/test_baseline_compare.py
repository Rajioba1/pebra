"""Phase E1 (unit): tolerance-based payload comparison. Decision is exact; scores within tolerance;
volatile identity fields (assessment_id/repo_id/guidance_packet_id) are excluded."""

from __future__ import annotations

from e2e.utils import baseline as bl


def test_passes_within_tolerance():
    actual = {"recommended_decision": "inspect_first", "scores": {"rau": 0.281}}
    base = {"recommended_decision": "inspect_first", "scores": {"rau": 0.28}}
    result = bl.compare_payload(actual, base, tolerance=0.02)
    assert result.passed is True
    assert result.diffs == []


def test_fails_on_decision_mismatch():
    actual = {"recommended_decision": "reject", "scores": {"rau": 0.28}}
    base = {"recommended_decision": "inspect_first", "scores": {"rau": 0.28}}
    result = bl.compare_payload(actual, base)
    assert result.passed is False
    assert any("decision" in d for d in result.diffs)


def test_fails_on_score_out_of_tolerance():
    actual = {"recommended_decision": "x", "scores": {"rau": 0.50}}
    base = {"recommended_decision": "x", "scores": {"rau": 0.28}}
    result = bl.compare_payload(actual, base, tolerance=0.02)
    assert result.passed is False
    assert any("rau" in d for d in result.diffs)


def test_excludes_volatile_identity_fields():
    actual = {"recommended_decision": "x", "scores": {}, "assessment_id": "asm_9", "repo_id": "r_2"}
    base = {"recommended_decision": "x", "scores": {}, "assessment_id": "asm_1", "repo_id": "r_1"}
    assert bl.compare_payload(actual, base).passed is True  # differing ids must not fail the compare
