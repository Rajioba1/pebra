"""M5b — pure apply_snapshot (learned override). Pure transform: AssessmentInput + SnapshotBundle ->
adjusted AssessmentInput. No DB, no wiring. v1 = most-specific-wins (k=1) REPLACEMENT (a learned
override, not a blended calibrated prior — blend is v2), clamped to [0.01, 0.99]."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pebra.core.apply_snapshot import SnapshotBundle, SnapshotFact, apply_snapshot
from pebra.core.models import AssessmentRequest, BenefitDeltaEvidence


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
          calibration_method="brier_bucket", weight=1.0, calibration_quality=1.0,
          scope_change_count=0):
    return SnapshotFact(
        fact_id=fact_id, target_type=target_type, target_name=target_name, scope_kind=scope_kind,
        scope_value=scope_value, specificity_rank=rank, value=value, sample_size=sample_size,
        created_at=created_at, requires_human_ratification=ratify, scope_json=scope_json or {},
        calibration_method=calibration_method, weight=weight,
        calibration_quality=calibration_quality, scope_change_count=scope_change_count,
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


def test_p_event_hard_replace_uses_monotone_risk_envelope_across_matching_scopes() -> None:
    features = {
        "symbol": {"symbol_id": "src/payments.py::charge", "change_kind": "CONTRACT"},
        "structural": {"symbol_fan_in_percentile": 0.95, "is_high_symbol_fan_in": True},
        "domain": {"matched_domains": ["payments"]},
    }
    inp = _inp(
        events=[{"event": "dependency_break", "p_event": 0.10}],
        features=features,
    )
    out = apply_snapshot(inp, _bundle(
        _fact(
            target_name="p_event.dependency_break", scope_kind="domain", scope_value="payments",
            rank=50, value=0.12, fact_id="lrf_domain",
        ),
        _fact(
            target_name="p_event.dependency_break", scope_kind="high_symbol_fan_in",
            rank=85, value=0.09, fact_id="lrf_high_fanin",
        ),
    ))

    assert out.events[0]["p_event"] == pytest.approx(0.12)
    (entry,) = out.applied_snapshot_provenance["applied_facts"]
    assert entry["winning_fact_id"] == "lrf_domain"


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


def test_default_hard_replace_drops_churned_ineligible_fact() -> None:
    out = apply_snapshot(
        _inp(p_success=0.70),
        _bundle(_fact(value=0.95, scope_change_count=200, fact_id="lrf_stale")),
    )
    assert out.p_success == 0.70
    assert out.applied_snapshot_provenance is None


def test_default_hard_replace_does_not_boost_weak_fact_weight() -> None:
    out = apply_snapshot(
        _inp(p_success=0.70),
        _bundle(_fact(value=0.95, weight=0.05, fact_id="lrf_weak")),
    )
    assert out.p_success == 0.70
    assert out.applied_snapshot_provenance is None


def test_malformed_reliability_fact_is_skipped_not_raised() -> None:
    out = apply_snapshot(
        _inp(p_success=0.70),
        _bundle(
            _fact(value=0.95, weight=-1.0, fact_id="lrf_bad"),
            _fact(value=0.80, fact_id="lrf_good"),
        ),
    )
    assert out.p_success == 0.80
    (entry,) = out.applied_snapshot_provenance["applied_facts"]
    assert entry["winning_fact_id"] == "lrf_good"


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


# --- benefit targets ----------------------------------------------------------


def test_benefit_binary_fact_overrides_immediate_benefit() -> None:
    out = apply_snapshot(
        _inp(),
        _bundle(_fact(
            target_type="benefit_binary",
            target_name="immediate_benefit_realized",
            value=0.8,
        )),
    )
    assert out.immediate_benefit == pytest.approx(0.8)
    assert out.applied_snapshot_provenance["applied_facts"][0]["target"] == "immediate_benefit_realized"


def test_benefit_continuous_delta_fact_updates_delta_evidence() -> None:
    inp = replace(
        _inp(),
        benefit_delta_evidence=BenefitDeltaEvidence(
            source_type="measured",
            deltas={"complexity_delta": -1.0},
            file_deltas={"src/a.py": {"complexity_delta": -1.0, "exposure_weight": 2.0}},
        ),
    )
    out = apply_snapshot(
        inp,
        _bundle(_fact(
            target_type="benefit_continuous",
            target_name="maintainability_delta.complexity_delta",
            value=-3.0,
        )),
    )
    assert out.benefit_delta_evidence.deltas["complexity_delta"] == pytest.approx(-3.0)
    assert out.benefit_delta_evidence.source_type == "learned_override"
    assert out.benefit_delta_evidence.file_deltas == {}


def test_measured_benefit_fact_sets_final_benefit_override() -> None:
    out = apply_snapshot(
        _inp(),
        _bundle(_fact(target_type="benefit_continuous", target_name="measured_benefit", value=0.9)),
    )
    assert out.benefit_override == pytest.approx(0.9)


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


# ============================ Step 3: top-k logit pooling (AD-20) ============================
# Default (no pool_config) and PoolConfig(mode="hard_replace") both use the same winner-take-all
# path after auto-apply eligibility filtering. log_pool is opt-in: combine the top-k facts in logit space,
# weighted by churn-decayed reliability, anchored to the model's prior via max_logit_shift.

import math  # noqa: E402

from pebra.core.apply_snapshot import PoolConfig  # noqa: E402

_CLAMP_LO, _CLAMP_HI = 0.01, 0.99


def _logit(p):
    p = max(_CLAMP_LO, min(_CLAMP_HI, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def test_pool_config_defaults_to_hard_replace() -> None:
    pc = PoolConfig()
    assert pc.mode == "hard_replace"
    assert pc.top_k == 1
    assert pc.max_logit_shift == pytest.approx(2.0)


@pytest.mark.parametrize(
    "cfg",
    [
        PoolConfig(mode="typo"),
        PoolConfig(mode="log_pool", top_k=0),
        PoolConfig(mode="log_pool", top_k=-1),
        PoolConfig(mode="log_pool", max_logit_shift=-0.1),
        PoolConfig(mode="log_pool", max_logit_shift=float("nan")),
    ],
)
def test_invalid_pool_config_rejected(cfg) -> None:
    with pytest.raises(ValueError):
        apply_snapshot(_inp(), _bundle(_fact()), cfg)


def test_pooling_fields_default_on_snapshot_fact() -> None:
    f = _fact()
    assert f.weight == pytest.approx(1.0)
    assert f.calibration_quality == pytest.approx(1.0)
    assert f.scope_change_count == 0


def test_default_pool_config_is_byte_identical_to_hard_replace() -> None:
    inp = _inp(p_success=0.70)
    bundle = _bundle(_fact(value=0.90))
    hard = apply_snapshot(inp, bundle)                    # default None -> hard replace
    explicit = apply_snapshot(inp, bundle, PoolConfig())  # explicit hard_replace mode
    assert explicit.p_success == hard.p_success == 0.90


def test_log_pool_combines_two_facts_in_logit_space() -> None:
    inp = _inp(p_success=0.50)
    bundle = _bundle(_fact(value=0.80, fact_id="lrf_a"), _fact(value=0.60, fact_id="lrf_b"))
    out = apply_snapshot(inp, bundle, PoolConfig(mode="log_pool", top_k=2, max_logit_shift=10.0))
    expected = _sigmoid((_logit(0.80) + _logit(0.60)) / 2.0)  # equal reliability -> simple mean
    assert out.p_success == pytest.approx(expected)


def test_log_pool_respects_top_k_limit() -> None:
    inp = _inp(p_success=0.50)
    bundle = _bundle(
        _fact(value=0.90, rank=3, fact_id="lrf_hi"),
        _fact(value=0.80, rank=2, fact_id="lrf_mid"),
        _fact(value=0.10, rank=1, fact_id="lrf_lo"),   # excluded by top_k=2
    )
    out = apply_snapshot(inp, bundle, PoolConfig(mode="log_pool", top_k=2, max_logit_shift=10.0))
    expected = _sigmoid((_logit(0.90) + _logit(0.80)) / 2.0)
    assert out.p_success == pytest.approx(expected)


def test_log_pool_clamps_to_max_logit_shift_from_prior() -> None:
    inp = _inp(p_success=0.50)  # logit(prior) == 0
    bundle = _bundle(_fact(value=0.999, fact_id="lrf_far"))
    out = apply_snapshot(inp, bundle, PoolConfig(mode="log_pool", top_k=1, max_logit_shift=2.0))
    assert out.p_success == pytest.approx(_sigmoid(2.0))  # clamped to prior + max_logit_shift
    assert out.p_success < 0.999


def test_log_pool_weights_by_reliability() -> None:
    inp = _inp(p_success=0.50)
    bundle = _bundle(
        _fact(value=0.90, calibration_quality=1.0, fact_id="lrf_strong"),
        _fact(value=0.10, calibration_quality=0.25, fact_id="lrf_weak"),
    )
    out = apply_snapshot(inp, bundle, PoolConfig(mode="log_pool", top_k=2, max_logit_shift=10.0))
    expected = _sigmoid((1.0 * _logit(0.90) + 0.25 * _logit(0.10)) / 1.25)
    assert out.p_success == pytest.approx(expected)


def test_log_pool_drops_churned_ineligible_fact() -> None:
    # single fact decayed below the auto-apply threshold -> not applied -> input unchanged.
    inp = _inp(p_success=0.70)
    bundle = _bundle(_fact(value=0.95, scope_change_count=200, fact_id="lrf_stale"))
    out = apply_snapshot(inp, bundle, PoolConfig(mode="log_pool", top_k=3, max_logit_shift=10.0))
    assert out.p_success == 0.70
    assert out.applied_snapshot_provenance is None


def test_log_pool_applies_to_p_event_targets_too() -> None:
    inp = _inp(events=[{"event": "test_regression", "p_event": 0.50}])
    bundle = _bundle(
        _fact(target_name="p_event.test_regression", value=0.80, fact_id="lrf_a"),
        _fact(target_name="p_event.test_regression", value=0.60, fact_id="lrf_b"),
    )
    out = apply_snapshot(inp, bundle, PoolConfig(mode="log_pool", top_k=2, max_logit_shift=10.0))
    expected = _sigmoid((_logit(0.80) + _logit(0.60)) / 2.0)
    assert out.events[0]["p_event"] == pytest.approx(expected)
