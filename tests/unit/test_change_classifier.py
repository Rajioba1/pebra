"""Architecture §5 / AD-27 — pure change classifier over parsed SymbolDiff rows.

The classifier never does I/O; the adapter parses ASTs and hands it rows. It maps flags to a
ChangeKind per symbol and summarizes the diff (max kind, consequential-symbol decision + reasons).
"""

from __future__ import annotations

from pebra.core import change_classifier as cc
from pebra.core.constants import ChangeKind
from pebra.core.models import SymbolDiffEvidence

DEFAULT_THRESHOLDS = {"consequential_symbol_fan_in_percentile": 0.90}


# --- is_high_fanin_consequential: assess-path helper over assembled SymbolDiffEvidence (M5c.5) ---


def test_high_fanin_on_behavioral_is_consequential() -> None:
    sde = SymbolDiffEvidence(max_change_kind="BEHAVIORAL", symbol_fan_in_percentile=0.95)
    assert cc.is_high_fanin_consequential(sde, 0.90) is True


def test_low_fanin_on_behavioral_is_not_consequential() -> None:
    sde = SymbolDiffEvidence(max_change_kind="BEHAVIORAL", symbol_fan_in_percentile=0.50)
    assert cc.is_high_fanin_consequential(sde, 0.90) is False


def test_high_fanin_on_cosmetic_is_not_consequential() -> None:
    # COSMETIC is not a consequence-bearing kind, so even max fan-in does not escalate
    sde = SymbolDiffEvidence(max_change_kind="COSMETIC", symbol_fan_in_percentile=0.99)
    assert cc.is_high_fanin_consequential(sde, 0.90) is False


def test_unknown_change_kind_is_consequential_kind() -> None:
    sde = SymbolDiffEvidence(max_change_kind="UNKNOWN", symbol_fan_in_percentile=0.95)
    assert cc.is_high_fanin_consequential(sde, 0.90) is True


def test_unparseable_change_kind_treated_as_unknown() -> None:
    sde = SymbolDiffEvidence(max_change_kind="NOT_A_KIND", symbol_fan_in_percentile=0.95)
    assert cc.is_high_fanin_consequential(sde, 0.90) is True


def _row(**kw):
    base = dict(
        symbol_id="m::f",
        visibility="internal",
        signature_changed=False,
        return_shape_changed=False,
        body_changed=False,
        control_flow_changed=False,
        external_side_effect_changed=False,
        db_write_changed=False,
        payment_api_changed=False,
        migration_changed=False,
        directive_comment_changed=False,
        test_only=False,
        callers_percentile=0.0,
        transitive_reaches_consequence_symbol=False,
    )
    base.update(kw)
    return base


def test_side_effect_dominates() -> None:
    assert cc.classify_symbol(_row(payment_api_changed=True)) is ChangeKind.SIDE_EFFECT
    assert cc.classify_symbol(_row(db_write_changed=True)) is ChangeKind.SIDE_EFFECT
    assert cc.classify_symbol(_row(migration_changed=True)) is ChangeKind.SIDE_EFFECT


def test_contract_from_signature_change() -> None:
    assert cc.classify_symbol(_row(signature_changed=True)) is ChangeKind.CONTRACT
    assert cc.classify_symbol(_row(return_shape_changed=True)) is ChangeKind.CONTRACT


def test_behavioral_from_body_change() -> None:
    assert cc.classify_symbol(_row(body_changed=True)) is ChangeKind.BEHAVIORAL


def test_directive_and_test_only_and_cosmetic() -> None:
    assert cc.classify_symbol(_row(directive_comment_changed=True)) is ChangeKind.DIRECTIVE
    assert cc.classify_symbol(_row(test_only=True)) is ChangeKind.TEST_ONLY
    assert cc.classify_symbol(_row()) is ChangeKind.COSMETIC


def test_worked_example_validate_login_is_behavioral_not_consequential() -> None:
    # internal symbol, behavioral body change, fan-in below threshold, no side effects.
    rows = [_row(symbol_id="src/auth.py::validate_login", visibility="internal",
                 body_changed=True, callers_percentile=0.42)]
    summary = cc.classify_diff(rows, DEFAULT_THRESHOLDS)
    assert summary.max_change_kind == "BEHAVIORAL"
    assert summary.consequential_symbol_changed is False
    assert summary.changed_symbols == ["src/auth.py::validate_login"]
    assert summary.visibility == "internal"


def test_exported_behavioral_symbol_is_consequential() -> None:
    rows = [_row(symbol_id="pkg::api", visibility="public_api", body_changed=True)]
    summary = cc.classify_diff(rows, DEFAULT_THRESHOLDS)
    assert summary.consequential_symbol_changed is True
    assert "visibility=public_api" in summary.consequence_reason


def test_high_fan_in_behavioral_symbol_is_consequential() -> None:
    rows = [_row(body_changed=True, callers_percentile=0.95)]
    summary = cc.classify_diff(rows, DEFAULT_THRESHOLDS)
    assert summary.consequential_symbol_changed is True


def test_max_change_kind_takes_most_severe_across_rows() -> None:
    rows = [_row(body_changed=True), _row(payment_api_changed=True)]
    summary = cc.classify_diff(rows, DEFAULT_THRESHOLDS)
    assert summary.max_change_kind == "SIDE_EFFECT"


def test_identity_replacement_suspected_classifies_as_contract() -> None:
    assert cc.classify_symbol(_row(identity_replacement_suspected=True)) is ChangeKind.CONTRACT


def test_side_effect_dominates_identity_replacement() -> None:
    row = _row(identity_replacement_suspected=True, payment_api_changed=True)
    assert cc.classify_symbol(row) is ChangeKind.SIDE_EFFECT


def test_no_rows_falls_back_to_unknown() -> None:
    summary = cc.classify_diff([], DEFAULT_THRESHOLDS)
    assert summary.max_change_kind == "UNKNOWN"
    assert summary.fallback_reason is not None
