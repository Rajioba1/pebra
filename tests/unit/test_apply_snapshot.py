"""M5b — pure apply_snapshot (learned override). Pure transform: AssessmentInput + SnapshotBundle ->
adjusted AssessmentInput. No DB, no wiring. v1 = most-specific-wins (k=1) REPLACEMENT (a learned
override, not a blended calibrated prior — blend is v2), clamped to [0.01, 0.99]."""

from __future__ import annotations

import pytest

from pebra.core.apply_snapshot import SnapshotBundle, SnapshotFact, apply_snapshot
from pebra.core.models import AssessmentRequest


def _inp(*, p_success=0.70, events=None, features=None, action_type="edit", expected_files=None):
    req = AssessmentRequest.single_action(
        task="t", action_id="a1", label="x", action_type=action_type,
        expected_files=expected_files or ["src/a.py"],
    )
    from pebra.core.models import AssessmentInput

    return AssessmentInput(
        request=req, action=req.candidate_actions[0], events=events or [], p_success=p_success,
        immediate_benefit=0.5, review_cost=0.1, criticality_stage="C2", criticality_value=0.5,
        edit_confidence_factors={}, thresholds={}, repo_id="r", repo_root="/x",
        structural_features=features,
    )


def _fact(target_name="p_success", scope_kind="global", scope_value="", rank=0, value=0.90,
          sample_size=50, created_at="2026-06-26T00:00:00Z", fact_id="lrf_1",
          target_type="risk_binary", ratify=False, scope_json=None,
          calibration_method="brier_bucket"):
    return SnapshotFact(
        fact_id=fact_id, target_type=target_type, target_name=target_name, scope_kind=scope_kind,
        scope_value=scope_value, specificity_rank=rank, value=value, sample_size=sample_size,
        created_at=created_at, requires_human_ratification=ratify, scope_json=scope_json or {},
        calibration_method=calibration_method,
    )


def _bundle(*facts):
    return SnapshotBundle(snapshot_id="rs_1", facts=tuple(facts))


# --- identity / no-op --------------------------------------------------------


def test_none_snapshot_is_identity() -> None:
    inp = _inp()
    assert apply_snapshot(inp, None) is inp


def test_empty_bundle_is_identity() -> None:
    inp = _inp()
    assert apply_snapshot(inp, _bundle()) is inp


def test_no_matching_facts_is_byte_equivalent() -> None:
    inp = _inp(p_success=0.7)
    out = apply_snapshot(inp, _bundle(_fact(target_name="p_event.nope")))
    assert out is inp  # no match -> same object returned, not a copy
    assert out.applied_snapshot_provenance is None


def test_equal_sample_tiebreak_prefers_newer_created_at() -> None:
    inp = _inp(p_success=0.70)
    out = apply_snapshot(inp, _bundle(
        _fact(rank=0, value=0.40, sample_size=50, created_at="2026-01-01T00:00:00Z", fact_id="lrf_old"),
        _fact(rank=0, value=0.80, sample_size=50, created_at="2026-06-26T00:00:00Z", fact_id="lrf_new"),
    ))
    assert out.p_success == 0.80  # equal rank + sample -> newest created_at wins


# --- p_success override ------------------------------------------------------


def test_global_p_success_override_and_provenance() -> None:
    inp = _inp(p_success=0.70)
    out = apply_snapshot(inp, _bundle(_fact(value=0.90)))
    assert out.p_success == 0.90
    prov = out.applied_snapshot_provenance
    assert prov["snapshot_id"] == "rs_1"
    (entry,) = prov["applied_facts"]
    assert entry["target"] == "p_success"
    assert entry["prior_predicted_p"] == 0.70 and entry["new_value"] == 0.90
    assert entry["winning_fact_id"] == "lrf_1" and entry["calibration_method"] == "brier_bucket"


def test_clamp_to_unit_safe_bounds() -> None:
    assert apply_snapshot(_inp(), _bundle(_fact(value=1.5))).p_success == 0.99
    assert apply_snapshot(_inp(), _bundle(_fact(value=0.0))).p_success == 0.01


# --- p_event override (only the matching event) ------------------------------


def test_p_event_override_targets_only_that_event() -> None:
    inp = _inp(events=[{"event": "test_regression", "p_event": 0.10},
                       {"event": "api_break", "p_event": 0.03}])
    out = apply_snapshot(inp, _bundle(_fact(target_name="p_event.test_regression", value=0.40)))
    assert out.events[0]["p_event"] == 0.40
    assert out.events[1]["p_event"] == 0.03  # untouched


# --- specificity + tiebreak --------------------------------------------------


def test_more_specific_scope_wins() -> None:
    feats = {"symbol": {"symbol_id": "src/a.py::foo"}, "domain": {"matched_domains": []}}
    inp = _inp(p_success=0.70, features=feats)
    out = apply_snapshot(inp, _bundle(
        _fact(scope_kind="global", rank=0, value=0.50, fact_id="lrf_g"),
        _fact(scope_kind="symbol", scope_value="src/a.py::foo", rank=100, value=0.95, fact_id="lrf_s"),
    ))
    assert out.p_success == 0.95  # symbol (rank 100) beats global (rank 0)


def test_equal_specificity_tiebreak_sample_then_recency_then_id() -> None:
    inp = _inp(p_success=0.70)
    out = apply_snapshot(inp, _bundle(
        _fact(rank=0, value=0.40, sample_size=10, fact_id="lrf_a"),
        _fact(rank=0, value=0.80, sample_size=99, fact_id="lrf_b"),  # higher sample_size wins
    ))
    assert out.p_success == 0.80


# --- gates: ratification + sample size --------------------------------------


def test_unratified_fact_not_applied() -> None:
    out = apply_snapshot(_inp(p_success=0.7), _bundle(_fact(value=0.9, ratify=True)))
    assert out.p_success == 0.7 and out.applied_snapshot_provenance is None


def test_zero_sample_fact_not_applied_defense_in_depth() -> None:
    out = apply_snapshot(_inp(p_success=0.7), _bundle(_fact(value=0.9, sample_size=0)))
    assert out.p_success == 0.7


def test_missing_calibration_method_not_applied() -> None:
    out = apply_snapshot(_inp(p_success=0.7), _bundle(_fact(value=0.9, calibration_method="")))
    assert out.p_success == 0.7 and out.applied_snapshot_provenance is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_not_applied(bad) -> None:
    # a malformed fact value must be SKIPPED, never clamped into a strong override
    out = apply_snapshot(_inp(p_success=0.7), _bundle(_fact(value=bad)))
    assert out.p_success == 0.7 and out.applied_snapshot_provenance is None


def test_path_glob_matches_any_edited_file_not_just_representative() -> None:
    # representative symbol is in src/a.py, but the action also edits src/payments/charge.py;
    # a fact scoped to src/payments/** must still apply
    inp = _inp(
        p_success=0.70,
        expected_files=["src/a.py", "src/payments/charge.py"],
        features={"symbol": {"file_path": "src/a.py"}, "domain": {"matched_domains": []}},
    )
    out = apply_snapshot(inp, _bundle(
        _fact(scope_kind="path_glob", scope_value="src/payments/*", rank=45, value=0.40)
    ))
    assert out.p_success == 0.40


def test_high_symbol_fan_in_scope_matches_schema_v2_feature() -> None:
    inp = _inp(
        p_success=0.70,
        features={
            "symbol": {"symbol_id": "src/payments.py::charge", "change_kind": "BEHAVIORAL"},
            # v2: per-symbol fan-in lives in the STRUCTURAL block (matches build_structural_features)
            "structural": {"symbol_fan_in_percentile": 0.94, "is_high_symbol_fan_in": True},
            "domain": {"matched_domains": []},
        },
    )
    out = apply_snapshot(inp, _bundle(
        _fact(scope_kind="high_symbol_fan_in", rank=85, value=0.42)
    ))
    assert out.p_success == 0.42


def test_high_symbol_fan_in_scope_matches_REAL_build_structural_features() -> None:
    # Regression guard: feed apply_snapshot the ACTUAL v2 payload (not a hand-built dict) so the scope
    # match is pinned to where build_structural_features really puts symbol fan-in (the structural block).
    from pebra.core import structural_features as sf

    feats = sf.build_structural_features(
        symbol_id="src/payments.py::charge", file_path="src/payments.py", action_type="edit",
        change_kind="BEHAVIORAL", visibility="internal", is_public_api=False, body_changed=True,
        signature_changed=False, container_file_fan_in_percentile=0.1, bridge_centrality=0.0,
        cycle_participation=False, is_architecture_anchor=False, domain_entrypoint=False, fan_out=0,
        dependency_boundary=False, matched_domains=[], domain_criticality_hint=None,
        criticality_stage="C3", symbol_fan_in_percentile=0.96, consequential_symbol_changed=True,
        provenance={},
    )
    out = apply_snapshot(_inp(p_success=0.70, features=feats),
                         _bundle(_fact(scope_kind="high_symbol_fan_in", rank=85, value=0.42)))
    assert out.p_success == 0.42


def test_domain_high_symbol_fan_in_scope_matches_domain_and_threshold() -> None:
    inp = _inp(
        p_success=0.70,
        features={
            "symbol": {"symbol_id": "src/payments.py::charge", "change_kind": "BEHAVIORAL"},
            "structural": {"symbol_fan_in_percentile": 0.88},
            "domain": {"matched_domains": ["payments"]},
        },
    )
    out = apply_snapshot(inp, _bundle(
        _fact(
            scope_kind="domain_high_symbol_fan_in",
            rank=75,
            value=0.43,
            scope_json={"domain": "payments", "min_percentile": 0.80},
        )
    ))
    assert out.p_success == 0.43


def test_domain_high_symbol_fan_in_scope_rejects_wrong_domain() -> None:
    inp = _inp(
        p_success=0.70,
        features={
            "structural": {"symbol_fan_in_percentile": 0.95},
            "domain": {"matched_domains": ["auth"]},
        },
    )
    out = apply_snapshot(inp, _bundle(
        _fact(
            scope_kind="domain_high_symbol_fan_in",
            rank=75,
            value=0.43,
            scope_json={"domain": "payments", "min_percentile": 0.80},
        )
    ))
    assert out is inp


# --- benefit targets explicitly skipped (v1 risk-only) -----------------------


def test_benefit_fact_is_ignored() -> None:
    out = apply_snapshot(
        _inp(p_success=0.7),
        _bundle(_fact(target_type="benefit_continuous", target_name="measured_benefit", value=0.9)),
    )
    assert out.p_success == 0.7 and out.applied_snapshot_provenance is None


# --- None structural_features fallback ---------------------------------------


def test_none_features_skips_symbol_scope_but_global_applies() -> None:
    inp = _inp(p_success=0.70, features=None)
    out = apply_snapshot(inp, _bundle(
        _fact(scope_kind="symbol", scope_value="src/a.py::foo", rank=100, value=0.95),
        _fact(scope_kind="global", rank=0, value=0.55, fact_id="lrf_g"),
    ))
    assert out.p_success == 0.55  # symbol fact unmatchable without features; global applies


# --- immutability ------------------------------------------------------------


def test_original_input_not_mutated() -> None:
    events = [{"event": "test_regression", "p_event": 0.10}]
    inp = _inp(p_success=0.70, events=events)
    out = apply_snapshot(inp, _bundle(
        _fact(value=0.90),
        _fact(target_name="p_event.test_regression", value=0.40, fact_id="lrf_e"),
    ))
    assert inp.p_success == 0.70                  # original scalar untouched
    assert inp.events[0]["p_event"] == 0.10       # original event dict untouched
    assert out.events is not inp.events           # adjusted events is a fresh list
    assert out.events[0] is not inp.events[0]     # and fresh dicts
