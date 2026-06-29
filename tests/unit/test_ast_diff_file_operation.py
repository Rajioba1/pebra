"""Destructive-op slice, Phase 4 — AstDiffAdapter sets the FileOperationKind axis from patch headers.

The file-op axis is INDEPENDENT of max_change_kind (symbol semantics): detection sets
file_operation_kind / file_operation_paths and never pollutes max_change_kind. RENAME/MOVE are
recorded (for the deferred import-graph model) but the controller only injects risk for DELETE.
"""

from __future__ import annotations

from pebra.adapters.ast_diff_adapter import AstDiffAdapter
from pebra.core.models import CandidateAction

_DELETE = ("diff --git a/src/config.py b/src/config.py\ndeleted file mode 100644\n"
           "--- a/src/config.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n")
_MODIFY = ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
           "@@ -1,1 +1,2 @@\n x = 1\n+y = 2\n")
_RENAME = ("diff --git a/src/foo.py b/src/bar.py\nsimilarity index 95%\n"
           "rename from src/foo.py\nrename to src/bar.py\n")


def _act(patch):
    return CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch)


def test_delete_sets_file_operation_axis_without_polluting_change_kind():
    sde = AstDiffAdapter().symbol_diff(_act(_DELETE), "/x")
    assert sde.file_operation_kind == "DELETE"
    assert sde.file_operation_paths == ("src/config.py",)
    assert sde.max_change_kind == "UNKNOWN"  # symbol-semantic axis untouched


def test_modify_is_none_operation():
    sde = AstDiffAdapter().symbol_diff(_act(_MODIFY), "/x")
    assert sde.file_operation_kind == "NONE"
    assert sde.file_operation_paths == ()


def test_rename_is_recorded():
    sde = AstDiffAdapter().symbol_diff(_act(_RENAME), "/x")
    assert sde.file_operation_kind == "RENAME"
    assert sde.file_operation_paths == ("src/foo.py",)


def test_delete_dominates_create_when_both_present():
    sde = AstDiffAdapter().symbol_diff(_act(_DELETE + _MODIFY), "/x")
    assert sde.file_operation_kind == "DELETE"


def test_supplied_evidence_file_operation_not_overridden():
    adapter = AstDiffAdapter({"file_operation_kind": "DELETE", "file_operation_paths": ("x.py",)})
    sde = adapter.symbol_diff(_act(_MODIFY), "/x")  # patch says modify, but evidence pre-set DELETE
    assert sde.file_operation_kind == "DELETE"
