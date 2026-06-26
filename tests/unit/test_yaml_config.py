"""Slice 4a — YamlConfigAdapter (ConfigPort impl). Loads .pebra.yml into PebraConfig.

Adapter layer: pyyaml is allowed here (forbidden in core). Missing config -> defaults; malformed
YAML fails clearly with the file path; the reserved `medium_auto_proceed_requires` warns and is
recorded but never evaluated (AD-6).
"""

from __future__ import annotations

import pytest

from pebra.adapters.yaml_config import YamlConfigAdapter
from pebra.ports.config_port import PebraConfig


def _write(tmp_path, text: str) -> str:
    (tmp_path / ".pebra.yml").write_text(text, encoding="utf-8")
    return str(tmp_path)


def test_missing_config_returns_defaults(tmp_path) -> None:
    cfg = YamlConfigAdapter().load_config(str(tmp_path))
    assert cfg == PebraConfig()


def test_empty_config_returns_defaults(tmp_path) -> None:
    cfg = YamlConfigAdapter().load_config(_write(tmp_path, ""))
    assert cfg.criticality_globs == []
    assert cfg.thresholds == {}


def test_criticality_glob_parsed(tmp_path) -> None:
    root = _write(tmp_path, 'criticality:\n  "src/payments/**": C4\n')
    cfg = YamlConfigAdapter().load_config(root)
    assert any(g.pattern == "src/payments/**" and g.stage == "C4" for g in cfg.criticality_globs)


def test_thresholds_parsed_as_floats(tmp_path) -> None:
    root = _write(tmp_path, "thresholds:\n  c3_max_expected_loss_without_human: 0.2\n")
    cfg = YamlConfigAdapter().load_config(root)
    assert cfg.thresholds["c3_max_expected_loss_without_human"] == 0.2


def test_edit_confidence_weights_fraction_notation(tmp_path) -> None:
    root = _write(tmp_path, 'edit_confidence_weights:\n  evidence_quality: "3/4"\n')
    cfg = YamlConfigAdapter().load_config(root)
    assert cfg.edit_confidence_weights.evidence_quality == 0.75
    assert cfg.edit_confidence_weights.p_success == 1.0  # unspecified factor keeps the default


def test_unknown_edit_confidence_weight_key_ignored(tmp_path) -> None:
    root = _write(tmp_path, "edit_confidence_weights:\n  not_a_factor: 0.5\n")
    cfg = YamlConfigAdapter().load_config(root)  # must not raise
    assert cfg.edit_confidence_weights.p_success == 1.0


def test_medium_auto_proceed_requires_warns_and_records_only_flag(tmp_path) -> None:
    root = _write(tmp_path, "medium_auto_proceed_requires:\n  - some_rule\n")
    with pytest.warns(UserWarning, match="medium_auto_proceed_requires"):
        cfg = YamlConfigAdapter().load_config(root)
    assert cfg.has_medium_auto_proceed_requires is True


def test_no_warning_when_reserved_key_absent(tmp_path) -> None:
    root = _write(tmp_path, "strict_mode: true\n")
    with warnings_as_errors():
        cfg = YamlConfigAdapter().load_config(root)
    assert cfg.has_medium_auto_proceed_requires is False


def test_malformed_yaml_raises_valueerror_with_path(tmp_path) -> None:
    root = _write(tmp_path, "criticality: [unclosed\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_non_mapping_top_level_raises(tmp_path) -> None:
    root = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_nan_threshold_raises_with_path(tmp_path) -> None:
    # .nan would make gate comparisons fail open once config is live -> reject at load.
    root = _write(tmp_path, "thresholds:\n  max_expected_loss_without_human: .nan\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_inf_threshold_raises_with_path(tmp_path) -> None:
    root = _write(tmp_path, "thresholds:\n  max_expected_loss_without_human: .inf\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_zero_threshold_raises(tmp_path) -> None:
    # 0 is degenerate (and a divisor in risk_budget_used) -> thresholds must be positive.
    root = _write(tmp_path, "thresholds:\n  max_expected_loss_without_human: 0\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_negative_threshold_raises(tmp_path) -> None:
    root = _write(tmp_path, "thresholds:\n  max_expected_loss_without_human: -0.5\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_inf_weight_raises(tmp_path) -> None:
    root = _write(tmp_path, "edit_confidence_weights:\n  evidence_quality: .inf\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_negative_weight_raises(tmp_path) -> None:
    root = _write(tmp_path, "edit_confidence_weights:\n  evidence_quality: -1\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_zero_weight_is_allowed(tmp_path) -> None:
    # weight 0 legitimately disables a factor -> finite & non-negative, accepted.
    root = _write(tmp_path, "edit_confidence_weights:\n  testability: 0\n")
    cfg = YamlConfigAdapter().load_config(root)
    assert cfg.edit_confidence_weights.testability == 0.0


def test_unknown_criticality_stage_raises_with_path(tmp_path) -> None:
    # a typo'd stage must fail clearly at load, not silently drop the rule.
    root = _write(tmp_path, 'criticality:\n  "src/**": C9\n')
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_strict_mode_default_false(tmp_path) -> None:
    root = _write(tmp_path, "thresholds: {}\n")
    assert YamlConfigAdapter().load_config(root).strict_mode is False


def test_strict_mode_true_parsed(tmp_path) -> None:
    root = _write(tmp_path, "strict_mode: true\n")
    assert YamlConfigAdapter().load_config(root).strict_mode is True


def test_null_threshold_value_raises_with_path(tmp_path) -> None:
    root = _write(tmp_path, "thresholds:\n  c3_max_expected_loss_without_human:\n")  # null value
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_non_numeric_threshold_raises_with_path(tmp_path) -> None:
    root = _write(tmp_path, "thresholds:\n  some_key: not_a_number\n")
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_zero_denominator_fraction_raises_with_path(tmp_path) -> None:
    root = _write(tmp_path, 'edit_confidence_weights:\n  evidence_quality: "3/0"\n')
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


def test_quoted_boolean_strict_mode_raises_not_silently_inverts(tmp_path) -> None:
    # `strict_mode: "false"` (quoted) must NOT silently become True — fail clearly instead.
    root = _write(tmp_path, 'strict_mode: "false"\n')
    with pytest.raises(ValueError, match="strict_mode"):
        YamlConfigAdapter().load_config(root)


def test_unreadable_config_raises_valueerror_not_raw_oserror(tmp_path, monkeypatch) -> None:
    root = _write(tmp_path, "strict_mode: true\n")

    def _boom(self, *a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", _boom)
    with pytest.raises(ValueError, match=r"\.pebra\.yml"):
        YamlConfigAdapter().load_config(root)


class warnings_as_errors:
    """Context manager: turn warnings into errors so a spurious warning fails the test."""

    def __enter__(self):
        import warnings

        self._cm = warnings.catch_warnings()
        self._cm.__enter__()
        warnings.simplefilter("error")
        return self

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)
