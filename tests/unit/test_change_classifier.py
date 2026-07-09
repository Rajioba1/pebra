"""Architecture §5 / AD-27 — pure change classifier over parsed SymbolDiff rows.

The classifier never does I/O; the adapter parses ASTs and hands it rows. It maps flags to a
ChangeKind per symbol and summarizes the diff (max kind, consequential-symbol decision + reasons).
"""

from __future__ import annotations

from pebra.core import change_classifier as cc
from pebra.core.constants import ChangeKind
from pebra.core.models import (
    FanInEvidence,
    MaterializedGraphDiffResult,
    MaterializedGraphDiffRow,
    SymbolDiffEvidence,
)

DEFAULT_THRESHOLDS = {"consequential_symbol_fan_in_percentile": 0.90}


# --- rows_from_fanin: the codegraph_structural coarse diff tier (multi-language) ---


def test_rows_from_fanin_exported_owner_is_contract_via_same_rule() -> None:
    # exported + body_changed -> CONTRACT, earned by the SAME classify_symbol rule the AST tier uses,
    # backed by graph facts (never a fabricated signature_changed).
    ev = FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        resolved_qualified_names=("Ns.Widget::Render",), resolved_symbol_count=1,
        symbol_fan_in_percentile=0.4, is_exported_contract=True,
    )
    rows = cc.rows_from_fanin(ev)
    assert len(rows) == 1
    assert rows[0]["symbol_id"] == "Ns.Widget::Render"
    assert rows[0]["body_changed"] is True
    assert rows[0]["visibility"] == "exported"
    assert "signature_changed" not in rows[0]  # the coarse tier never claims signature detail
    summary = cc.classify_diff(rows, DEFAULT_THRESHOLDS)
    assert summary.max_change_kind == ChangeKind.CONTRACT.value


def test_rows_from_fanin_internal_owner_is_behavioral_not_contract() -> None:
    ev = FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        resolved_qualified_names=("Ns.Widget::_helper",), resolved_symbol_count=1,
        is_exported_contract=False, is_abstract_or_interface_contract=False,
    )
    summary = cc.classify_diff(cc.rows_from_fanin(ev), DEFAULT_THRESHOLDS)
    assert summary.max_change_kind == ChangeKind.BEHAVIORAL.value  # touched, but not a public surface


def test_rows_from_fanin_abstract_surface_counts_as_exported() -> None:
    ev = FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        resolved_qualified_names=("IShape",), resolved_symbol_count=1,
        is_abstract_or_interface_contract=True,
    )
    assert cc.rows_from_fanin(ev)[0]["visibility"] == "exported"


def test_rows_from_fanin_high_fanin_is_consequential() -> None:
    ev = FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        resolved_qualified_names=("hot",), resolved_symbol_count=1,
        symbol_fan_in_percentile=0.97, is_exported_contract=True,
    )
    summary = cc.classify_diff(cc.rows_from_fanin(ev), DEFAULT_THRESHOLDS)
    assert summary.consequential_symbol_changed is True


def test_rows_from_fanin_no_owners_is_empty() -> None:
    assert cc.rows_from_fanin(FanInEvidence(resolution_method="unresolved")) == []


# --- rows_from_materialized_graph_diff: the codegraph_semantic tier ---


def _sem_fanin(**over):
    return FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        resolved_qualified_names=over.pop("resolved_qualified_names", ("f",)),
        resolved_file_paths=over.pop("resolved_file_paths", ("src/a.ts",)),
        resolved_symbol_count=over.pop("resolved_symbol_count", 1),
        symbol_fan_in_percentile=over.pop("symbol_fan_in_percentile", 0.1),
        **over)


def _diff_row(**over):
    return MaterializedGraphDiffRow(
        file_path=over.pop("file_path", "src/a.ts"), qualified_name=over.pop("qualified_name", "f"),
        language="typescript",
        operation=over.pop("operation", "modified"),
        kind=over.pop("kind", "function"),
        signature_changed=over.pop("signature_changed", False),
        return_type_changed=over.pop("return_type_changed", False),
        visibility_changed=over.pop("visibility_changed", False),
        is_abstract=over.pop("is_abstract", None),
        is_abstract_changed=over.pop("is_abstract_changed", None))


def test_semantic_signature_change_enriches_the_coarse_floor_to_contract() -> None:
    result = MaterializedGraphDiffResult(available=True, rows=(_diff_row(signature_changed=True),))
    rows = cc.rows_from_materialized_graph_diff(result, _sem_fanin())
    assert len(rows) == 1
    assert rows[0]["signature_changed"] is True
    assert rows[0]["body_changed"] is True  # coarse floor PRESERVED (never masked)
    assert cc.classify_diff(rows, DEFAULT_THRESHOLDS).max_change_kind == ChangeKind.CONTRACT.value


def test_semantic_body_only_change_keeps_floor_and_is_not_masked() -> None:
    # owner touched (fanin resolved it) but signature/return/visibility all unchanged -> floor only,
    # body_changed=True stays, so a body-only rewrite is never dropped.
    result = MaterializedGraphDiffResult(available=True, rows=(_diff_row(),))  # nothing changed
    rows = cc.rows_from_materialized_graph_diff(result, _sem_fanin(is_exported_contract=True))
    assert len(rows) == 1 and rows[0]["body_changed"] is True and "signature_changed" not in rows[0]
    assert cc.classify_diff(rows, DEFAULT_THRESHOLDS).max_change_kind == ChangeKind.CONTRACT.value


def test_semantic_signatureless_visibility_change_still_enriches() -> None:
    # BUG-4: a partial-signature owner (signature_changed None) with a real visibility change must still
    # enrich the row's visibility_changed -> CONTRACT via classify_symbol.
    result = MaterializedGraphDiffResult(
        available=True, rows=(_diff_row(signature_changed=None, visibility_changed=True),))
    rows = cc.rows_from_materialized_graph_diff(result, _sem_fanin())
    assert rows[0]["visibility_changed"] is True and rows[0]["body_changed"] is True
    assert cc.classify_diff(rows, DEFAULT_THRESHOLDS).max_change_kind == ChangeKind.CONTRACT.value


def test_semantic_unavailable_diff_returns_pure_coarse_floor() -> None:
    result = MaterializedGraphDiffResult(available=False, fallback_reason="x")
    rows = cc.rows_from_materialized_graph_diff(result, _sem_fanin())
    assert rows == cc.rows_from_fanin(_sem_fanin())  # identical to the coarse tier


def test_semantic_does_not_attribute_another_owners_change_to_this_owner() -> None:
    # Identity-safety: fanin resolved owner "f"; the ONLY changed diff row is a DIFFERENT owner "g"
    # (e.g. from another touched file). "f" must NOT be enriched with "g"'s signature change — the
    # floor is kept, no fabricated CONTRACT claim about "f".
    result = MaterializedGraphDiffResult(
        available=True, rows=(_diff_row(qualified_name="g", signature_changed=True),))
    rows = cc.rows_from_materialized_graph_diff(result, _sem_fanin(resolved_qualified_names=("f",)))
    assert rows == cc.rows_from_fanin(_sem_fanin(resolved_qualified_names=("f",)))  # pure floor
    assert "signature_changed" not in rows[0]


def test_semantic_does_not_attribute_same_named_owner_in_another_file() -> None:
    # The materialized tier is keyed by (file_path, qualified_name). Matching only by qualified_name
    # fabricates facts when two files both contain owner "f"; the fan-in owner path must match too.
    result = MaterializedGraphDiffResult(
        available=True,
        rows=(_diff_row(file_path="src/b.ts", qualified_name="f", signature_changed=True),),
    )
    fanin = _sem_fanin(resolved_qualified_names=("f",), resolved_file_paths=("src/a.ts",))
    rows = cc.rows_from_materialized_graph_diff(result, fanin)

    assert rows == cc.rows_from_fanin(fanin)
    assert "signature_changed" not in rows[0]


def test_semantic_multi_owner_patch_keeps_floor_no_enrichment() -> None:
    # two touched owners + one changed diff row -> ambiguous join -> pure coarse floor for BOTH owners
    # (the dangerous owner is never dropped; enrichment is simply forgone).
    result = MaterializedGraphDiffResult(available=True, rows=(_diff_row(signature_changed=True),))
    fanin = _sem_fanin(resolved_qualified_names=("f", "g"), resolved_symbol_count=2)
    rows = cc.rows_from_materialized_graph_diff(result, fanin)
    assert len(rows) == 2 and all(r["body_changed"] is True for r in rows)
    assert all("signature_changed" not in r for r in rows)


def test_semantic_added_abstract_member_enriches_floor_to_contract() -> None:
    result = MaterializedGraphDiffResult(available=True, rows=(
        _diff_row(
            qualified_name="ZodType._pebraDescribe",
            operation="added",
            kind="method",
            is_abstract=True,
            signature_changed=True,
        ),
    ))
    rows = cc.rows_from_materialized_graph_diff(result, _sem_fanin(resolved_qualified_names=("ZodType",)))

    assert rows[0]["abstract_contract_member_changed"] is True
    assert rows[0]["body_changed"] is True
    assert rows[0]["materialized_operation"] == "added"
    assert cc.classify_diff(rows, DEFAULT_THRESHOLDS).max_change_kind == ChangeKind.CONTRACT.value


def test_semantic_added_concrete_member_preserves_floor_without_abstract_contract() -> None:
    result = MaterializedGraphDiffResult(available=True, rows=(
        _diff_row(
            qualified_name="ZodType._pebraDescribe",
            operation="added",
            kind="method",
            is_abstract=False,
            signature_changed=True,
        ),
    ))
    rows = cc.rows_from_materialized_graph_diff(result, _sem_fanin(resolved_qualified_names=("ZodType",)))

    assert rows == cc.rows_from_fanin(_sem_fanin(resolved_qualified_names=("ZodType",)))
    assert rows[0]["body_changed"] is True
    assert "abstract_contract_member_changed" not in rows[0]
    assert cc.classify_diff(rows, DEFAULT_THRESHOLDS).max_change_kind == ChangeKind.BEHAVIORAL.value


def test_semantic_same_file_unrelated_added_member_keeps_floor() -> None:
    result = MaterializedGraphDiffResult(available=True, rows=(
        _diff_row(
            qualified_name="Other._pebraDescribe",
            operation="added",
            kind="method",
            is_abstract=True,
        ),
    ))
    fanin = _sem_fanin(resolved_qualified_names=("ZodType",), resolved_file_paths=("src/a.ts",))
    rows = cc.rows_from_materialized_graph_diff(result, fanin)

    assert rows == cc.rows_from_fanin(fanin)


def test_semantic_multiple_added_removed_members_keep_floor() -> None:
    result = MaterializedGraphDiffResult(available=True, rows=(
        _diff_row(qualified_name="ZodType.a", operation="added", kind="method", is_abstract=True),
        _diff_row(qualified_name="ZodType.b", operation="added", kind="method", is_abstract=True),
    ))
    fanin = _sem_fanin(resolved_qualified_names=("ZodType",), resolved_file_paths=("src/a.ts",))
    rows = cc.rows_from_materialized_graph_diff(result, fanin)

    assert rows == cc.rows_from_fanin(fanin)


def test_visibility_change_classifies_as_contract_when_signature_is_comparable() -> None:
    row = _row(visibility_changed=True)
    assert cc.classify_symbol(row) is ChangeKind.CONTRACT


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
