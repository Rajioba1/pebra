from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.continuity import fit


def _row(
    owner: str,
    *,
    proof_fired: bool,
    consumer_passed: bool,
    fit_eligible: bool = True,
    action_success: bool | None = None,
) -> dict[str, object]:
    return {
        "owner_cluster_id": owner,
        "calibration_fit_eligible": fit_eligible,
        "proof_fired": proof_fired,
        "consumer_test_ran": True,
        "consumer_test_passed": consumer_passed,
        "action_success": consumer_passed if action_success is None else action_success,
        "language_tier": "full",
        "proof_class": (
            "proof_fired_consumer_passed"
            if proof_fired and consumer_passed
            else "proof_fired_consumer_failed"
            if proof_fired
            else "proof_unavailable_consumer_passed"
        ),
    }


def test_fit_uses_one_observation_per_independent_owner() -> None:
    rows = [
        _row("owner-a", proof_fired=True, consumer_passed=True),
        _row("owner-a", proof_fired=False, consumer_passed=False),
        _row("owner-b", proof_fired=True, consumer_passed=True),
        _row("owner-c", proof_fired=True, consumer_passed=False),
        _row("owner-d", proof_fired=True, consumer_passed=True),
    ]

    result = fit.fit_rows(rows, minimum_owner_clusters=4)

    assert result.sample_size == 4
    assert result.successes == 3
    assert result.p_success == pytest.approx(4 / 6)
    assert result.p_success_variance == pytest.approx((4 * 2) / ((6**2) * 7))
    assert result.p_success_aleatoric_variance == pytest.approx((4 * 2) / (6 * 7))


def test_fit_fails_closed_on_duplicate_proof_rows_for_one_owner() -> None:
    rows = [
        _row("owner-a", proof_fired=True, consumer_passed=True),
        _row("owner-a", proof_fired=True, consumer_passed=False),
        _row("owner-b", proof_fired=True, consumer_passed=True),
        _row("owner-c", proof_fired=True, consumer_passed=True),
        _row("owner-d", proof_fired=True, consumer_passed=True),
    ]

    with pytest.raises(ValueError, match="exactly one proof-fired row"):
        fit.fit_rows(rows, minimum_owner_clusters=4)


def test_fit_requires_multiple_independent_owner_clusters() -> None:
    rows = [_row("owner-a", proof_fired=True, consumer_passed=True)]

    with pytest.raises(ValueError, match="independent owner clusters"):
        fit.fit_rows(rows, minimum_owner_clusters=3)


def test_fit_excludes_provider_coverage_rows_not_selected_for_calibration() -> None:
    rows = [
        _row("owner-a", proof_fired=True, consumer_passed=True),
        _row("owner-b", proof_fired=True, consumer_passed=True),
        _row("owner-c", proof_fired=True, consumer_passed=True),
        _row("owner-d", proof_fired=True, consumer_passed=False, fit_eligible=False),
    ]

    result = fit.fit_rows(rows, minimum_owner_clusters=3)

    assert result.sample_size == 3
    assert result.successes == 3
    assert "owner-d" not in result.owner_cluster_ids


def test_fit_uses_full_action_success_not_consumer_check_alone() -> None:
    rows = [
        _row("owner-a", proof_fired=True, consumer_passed=True, action_success=False),
        _row("owner-b", proof_fired=True, consumer_passed=True),
        _row("owner-c", proof_fired=True, consumer_passed=True),
    ]

    result = fit.fit_rows(rows, minimum_owner_clusters=3)

    assert result.successes == 2
    assert result.p_success == pytest.approx(3 / 5)


def test_fit_report_is_stable_and_scoped(tmp_path: Path) -> None:
    rows = [
        _row(owner, proof_fired=True, consumer_passed=True)
        for owner in ("owner-a", "owner-b", "owner-c", "owner-d")
    ]
    source = tmp_path / "observations.jsonl"
    source.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    first = fit.fit_file(source, minimum_owner_clusters=4)
    second = fit.fit_file(source, minimum_owner_clusters=4)

    assert fit.to_json(first) == fit.to_json(second)
    assert first.calibration_tag == "zod_single_repo_provisional_v1"
    assert first.action_type == "edit"
    assert first.language == "typescript"
    assert first.language_tier == "full"
    assert first.graph_fact_kind == "exported_binding_continuity"


def test_frozen_prior_must_match_reviewed_fit() -> None:
    rows = [
        _row(owner, proof_fired=True, consumer_passed=True)
        for owner in (
            "zod-v3-addIssueToContext",
            "zod-v3-getErrorMap",
            "zod-v3-setErrorMap",
        )
    ]
    result = fit.fit_rows(rows)

    fit.verify_frozen_prior(result)
    with pytest.raises(ValueError, match="frozen provisional prior does not match"):
        fit.verify_frozen_prior(replace(result, p_success=0.79))
