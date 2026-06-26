"""Phase 2 (Slice 1) — config schema (ports/config_port.py). Pure data contract for .pebra.yml.

PebraConfig lives with its port (the RepoMetadata pattern) since core never consumes it.
"""

from __future__ import annotations

from pebra.ports.config_port import CriticalityGlob, EditConfidenceWeights, PebraConfig


def test_pebra_config_defaults_are_empty() -> None:
    cfg = PebraConfig()
    assert cfg.criticality_globs == []
    assert cfg.thresholds == {}
    assert cfg.has_medium_auto_proceed_requires is False
    assert isinstance(cfg.edit_confidence_weights, EditConfidenceWeights)


def test_criticality_glob_holds_pattern_and_stage() -> None:
    g = CriticalityGlob(pattern="src/payments/**", stage="C4")
    assert g.pattern == "src/payments/**"
    assert g.stage == "C4"


def test_edit_confidence_weights_default_to_equal() -> None:
    w = EditConfidenceWeights()
    assert w.p_success == 1.0
    assert w.scope_control == 1.0


def test_pebra_config_carries_globs_and_thresholds() -> None:
    cfg = PebraConfig(
        criticality_globs=[CriticalityGlob("src/auth/**", "C3")],
        thresholds={"c3_max_expected_loss_without_human": 0.20},
        has_medium_auto_proceed_requires=True,
    )
    assert cfg.criticality_globs[0].stage == "C3"
    assert cfg.thresholds["c3_max_expected_loss_without_human"] == 0.20
    assert cfg.has_medium_auto_proceed_requires is True
