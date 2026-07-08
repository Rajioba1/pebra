"""Architecture §5/AD-27 — real AST symbol-diff (post-edit reclassification input).

compute_symbol_diff_rows parses before/after Python source and emits SymbolDiff rows that
change_classifier consumes. Pure (source strings in, row dicts out). Visibility is conservative
(underscore=private, else internal) so a plain body edit does not falsely escalate to CONTRACT.
"""

from __future__ import annotations

from pebra.adapters.ast_diff_adapter import compute_symbol_diff_rows
from pebra.core import change_classifier as cc

BEFORE = "def validate_login(u, p):\n    return True\n"


def _kinds(rows):
    return cc.classify_diff(rows, {}).max_change_kind


def test_body_change_is_behavioral() -> None:
    after = "def validate_login(u, p):\n    return bool(u and p)\n"
    rows = compute_symbol_diff_rows(BEFORE, after, "src/auth.py")
    assert len(rows) == 1
    assert rows[0]["symbol_id"] == "src/auth.py::validate_login"
    assert rows[0]["body_changed"] is True
    assert rows[0]["signature_changed"] is False
    assert rows[0]["visibility"] == "internal"
    assert _kinds(rows) == "BEHAVIORAL"


def test_signature_change_is_contract() -> None:
    after = "def validate_login(u, p, mfa):\n    return True\n"
    rows = compute_symbol_diff_rows(BEFORE, after, "src/auth.py")
    assert rows[0]["signature_changed"] is True
    assert _kinds(rows) == "CONTRACT"


def test_return_type_annotation_change_is_a_signature_change() -> None:
    # a pure return-type change (identical body) must not be silently dropped — it's a CONTRACT change
    before = "def f(x) -> None:\n    return None\n"
    after = "def f(x) -> dict:\n    return None\n"
    rows = compute_symbol_diff_rows(before, after, "m.py")
    assert len(rows) == 1
    assert rows[0]["signature_changed"] is True
    assert _kinds(rows) == "CONTRACT"


def test_unchanged_function_yields_no_rows() -> None:
    rows = compute_symbol_diff_rows(BEFORE, BEFORE, "src/auth.py")
    assert rows == []


def test_new_file_added_functions_are_behavioral() -> None:
    rows = compute_symbol_diff_rows(None, "def charge():\n    return 1\n", "src/payments.py")
    assert rows[0]["symbol_id"] == "src/payments.py::charge"
    assert rows[0]["body_changed"] is True


def test_private_function_visibility() -> None:
    before = "def _helper():\n    return 1\n"
    after = "def _helper():\n    return 2\n"
    rows = compute_symbol_diff_rows(before, after, "m.py")
    assert rows[0]["visibility"] == "private"


def test_method_changes_are_internal_visibility() -> None:
    before = "class A:\n    def go(self):\n        return 1\n"
    after = "class A:\n    def go(self):\n        return 2\n"
    rows = compute_symbol_diff_rows(before, after, "m.py")
    assert rows[0]["symbol_id"] == "m.py::A.go"
    assert rows[0]["visibility"] == "internal"


def test_syntax_error_after_yields_no_rows_not_crash() -> None:
    rows = compute_symbol_diff_rows(BEFORE, "def broken(:\n", "src/auth.py")
    assert rows == []


# --- M4: conservative identity-replacement detection (body-only similarity, threshold 0.5) ---

_REWRITE_BEFORE = (
    "def process(data):\n    result = []\n    for x in data:\n"
    "        if x > 0:\n            result.append(x * 2)\n    return result\n"
)
_REWRITE_AFTER = (
    "def process(data):\n    lookup = {}\n    for key in data:\n"
    "        lookup[key] = hash(key) % 100\n    return sorted(lookup.values())\n"
)
_REFACTOR_BEFORE = (
    "def calc(items):\n    total = 0\n    for it in items:\n        total += it.price\n    return total\n"
)
_REFACTOR_AFTER = (
    "def calc(items):\n    running = 0\n    for entry in items:\n"
    "        running += entry.price\n    return running\n"
)


def test_total_body_replacement_sets_identity_suspected() -> None:
    rows = compute_symbol_diff_rows(_REWRITE_BEFORE, _REWRITE_AFTER, "m.py")
    assert rows[0]["signature_changed"] is False
    assert rows[0]["identity_replacement_suspected"] is True


def test_total_body_replacement_classifies_as_contract() -> None:
    rows = compute_symbol_diff_rows(_REWRITE_BEFORE, _REWRITE_AFTER, "m.py")
    assert _kinds(rows) == "CONTRACT"


def test_legit_refactor_does_not_set_identity_suspected() -> None:
    rows = compute_symbol_diff_rows(_REFACTOR_BEFORE, _REFACTOR_AFTER, "m.py")
    assert rows[0]["identity_replacement_suspected"] is False
    assert _kinds(rows) == "BEHAVIORAL"


def test_small_body_change_is_not_identity_replacement() -> None:
    after = "def validate_login(u, p):\n    return bool(u and p)\n"
    rows = compute_symbol_diff_rows(BEFORE, after, "src/auth.py")
    assert rows[0]["identity_replacement_suspected"] is False


def test_added_symbol_is_not_identity_replacement() -> None:
    rows = compute_symbol_diff_rows(None, "def charge():\n    return 1\n", "m.py")
    assert rows[0]["identity_replacement_suspected"] is False


# --- docstrings/comments must not register as a body (semantic) change (anti-nuisance) ---

def test_docstring_only_change_yields_no_semantic_row() -> None:
    before = 'def f(x):\n    """Old docstring."""\n    return x\n'
    after = 'def f(x):\n    """A completely different docstring now."""\n    return x\n'
    assert compute_symbol_diff_rows(before, after, "m.py") == []


def test_docstring_plus_real_body_change_is_still_behavioral() -> None:
    before = 'def f(x):\n    """Old."""\n    return x\n'
    after = 'def f(x):\n    """New."""\n    return x + 1\n'
    rows = compute_symbol_diff_rows(before, after, "m.py")
    assert rows[0]["body_changed"] is True
    assert _kinds(rows) == "BEHAVIORAL"


def test_docstring_only_stub_change_yields_no_row() -> None:
    before = 'def f(x):\n    """Old."""\n'
    after = 'def f(x):\n    """New."""\n'
    assert compute_symbol_diff_rows(before, after, "m.py") == []


# --- module/class-level semantic edits (no function row) must not look cosmetic ---

def test_module_level_constant_change_emits_module_row() -> None:
    rows = compute_symbol_diff_rows("TAX_RATE = 0.1\n", "TAX_RATE = 0.2\n", "m.py")
    assert len(rows) == 1
    assert rows[0]["symbol_id"] == "m.py::__module__"
    assert _kinds(rows) == "BEHAVIORAL"


def test_added_module_level_constant_emits_module_row() -> None:
    rows = compute_symbol_diff_rows(None, "TAX_RATE = 0.1\n", "m.py")
    assert len(rows) == 1
    assert rows[0]["symbol_id"] == "m.py::__module__"
    assert _kinds(rows) == "BEHAVIORAL"


def test_deleted_module_level_constant_emits_module_row() -> None:
    rows = compute_symbol_diff_rows("TAX_RATE = 0.1\n", None, "m.py")
    assert len(rows) == 1
    assert rows[0]["symbol_id"] == "m.py::__module__"
    assert _kinds(rows) == "BEHAVIORAL"


def test_import_change_emits_module_row() -> None:
    rows = compute_symbol_diff_rows("import os\n", "import sys\n", "m.py")
    assert rows and rows[0]["symbol_id"] == "m.py::__module__"


def test_decorator_only_change_emits_module_row() -> None:
    before = "@a\ndef f():\n    return 1\n"
    after = "@b\ndef f():\n    return 1\n"
    rows = compute_symbol_diff_rows(before, after, "m.py")
    assert rows and rows[0]["symbol_id"] == "m.py::__module__"


def test_class_base_change_emits_module_row() -> None:
    rows = compute_symbol_diff_rows("class C(A):\n    x = 1\n", "class C(B):\n    x = 1\n", "m.py")
    assert rows and rows[0]["symbol_id"] == "m.py::__module__"


def test_module_docstring_only_change_is_not_a_module_row() -> None:
    rows = compute_symbol_diff_rows('"""Old mod."""\nx = 1\n', '"""New mod."""\nx = 1\n', "m.py")
    assert rows == []
