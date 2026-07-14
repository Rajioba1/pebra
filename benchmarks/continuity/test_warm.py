"""Deterministic policy checks for cold, shipped, and repository-local priors."""

from __future__ import annotations

from pebra.core.constants import Decision

from benchmarks.continuity import warm


def test_safe_evidence_improves_rau_without_changing_expected_loss() -> None:
    rows = warm.run_probe()
    cold = rows["cold_safe"]
    shipped = rows["shipped_safe"]
    local = rows["local_safe"]

    assert shipped.expected_loss == cold.expected_loss
    assert local.expected_loss == cold.expected_loss
    assert shipped.rau > cold.rau
    assert local.rau > cold.rau
    assert shipped.prior_source == "shipped"
    assert local.prior_source == "local_learned"


def test_harmful_candidate_stays_restricted_under_every_prior_source() -> None:
    rows = warm.run_probe()

    for case_id in ("cold_harmful", "shipped_harmful", "local_harmful"):
        assert rows[case_id].decision is not Decision.PROCEED
        assert rows[case_id].expected_loss > rows[case_id].effective_threshold
    assert rows["local_harmful"].consequence_risk_floor_applied is True


def test_applied_variances_are_bounded_and_probe_is_deterministic() -> None:
    first = warm.run_probe()
    second = warm.run_probe()

    assert warm.to_json(first) == warm.to_json(second)
    for case_id in ("shipped_safe", "local_safe", "shipped_harmful", "local_harmful"):
        row = first[case_id]
        assert row.p_success_variance_floor <= row.p_success_variance <= row.p_success_variance_cap
        assert row.review_cost_variance_floor <= row.review_cost_variance <= row.review_cost_variance_cap


def test_probe_is_explicitly_not_calibration_evidence() -> None:
    payload = warm.to_payload(warm.run_probe())

    assert payload["schema_version"] == "continuity-warm-probe-v1"
    assert payload["evidence_class"] == "synthetic_policy_probe"
    assert payload["calibration_eligible"] is False
