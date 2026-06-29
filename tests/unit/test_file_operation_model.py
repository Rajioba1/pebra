"""Destructive-op slice, Phase 1 — core vocabulary.

FileOperationKind is a SEPARATE axis from ChangeKind (file ops vs symbol-semantic change are
orthogonal: a file can be deleted AND its symbols had a contract change). FileFanInRollup carries the
aggregate call fan-in across ALL symbols in a file (for whole-file deletion). structural_features
exposes the rolled-up percentile.
"""

from __future__ import annotations

from dataclasses import fields

from pebra.core import structural_features as sf
from pebra.core.constants import ChangeKind, FileOperationKind
from pebra.core.models import AssessmentInput, FileFanInRollup, SymbolDiffEvidence


def test_file_operation_kind_values():
    assert {k.value for k in FileOperationKind} == {"NONE", "DELETE", "CREATE", "RENAME", "MOVE"}


def test_file_operation_kind_is_separate_axis_from_change_kind():
    # file ops must NOT pollute the symbol-semantic ChangeKind enum.
    change_values = {k.value for k in ChangeKind}
    for v in ("DELETE", "RENAME", "MOVE", "CREATE", "DELETE_FILE"):
        assert v not in change_values


def test_file_fanin_rollup_defaults_unresolved():
    r = FileFanInRollup()
    assert r.resolution_method == "unresolved"
    assert r.distinct_caller_count == 0
    assert r.max_caller_count == 0
    assert r.symbol_count == 0
    assert r.file_symbol_fanin_rollup_percentile == 0.0


def test_symbol_diff_evidence_has_file_operation_axis_defaulting_none():
    sde = SymbolDiffEvidence()
    assert sde.file_operation_kind == "NONE"
    assert sde.file_operation_paths == ()
    # the file-op axis is independent of the symbol-semantic max_change_kind.
    assert sde.max_change_kind == "UNKNOWN"


def test_assessment_input_carries_file_fanin_rollup_default_none():
    by_name = {f.name: f for f in fields(AssessmentInput)}
    assert "file_fanin_rollup" in by_name
    assert by_name["file_fanin_rollup"].default is None


def _feats(**over):
    base = dict(
        symbol_id="src/a.py::foo", file_path="src/a.py", action_type="edit",
        change_kind="BEHAVIORAL", visibility="internal", is_public_api=False, body_changed=True,
        signature_changed=False, container_file_fan_in_percentile=0.1, bridge_centrality=0.0,
        cycle_participation=False, is_architecture_anchor=False, domain_entrypoint=False, fan_out=0,
        dependency_boundary=False, matched_domains=[], domain_criticality_hint=None,
        criticality_stage="C2", symbol_fan_in_percentile=0.0, consequential_symbol_changed=False,
        provenance={},
    )
    base.update(over)
    return sf.build_structural_features(**base)


def test_structural_features_rollup_defaults_zero_and_not_high():
    st = _feats()["structural"]
    assert st["file_symbol_fanin_rollup_percentile"] == 0.0
    assert st["is_high_file_symbol_fanin_rollup"] is False


def test_structural_features_rollup_high_at_anchor_threshold():
    st = _feats(file_symbol_fanin_rollup_percentile=0.95)["structural"]
    assert st["file_symbol_fanin_rollup_percentile"] == 0.95
    assert st["is_high_file_symbol_fanin_rollup"] is True


def test_structural_features_capture_file_operation_context():
    feats = _feats(file_operation_kind="DELETE", file_operation_path_count=2)

    assert feats["symbol"]["file_operation_kind"] == "DELETE"
    assert feats["symbol"]["file_operation_path_count"] == 2
