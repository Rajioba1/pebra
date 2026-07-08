"""Architecture §9/AD-27 — GitChangeVerifier reads the scope-correct 'after' and records deltas."""

from __future__ import annotations

import subprocess

from pebra.adapters.git_change_verifier import GitChangeVerifier


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init(tmp_path, content):
    (tmp_path / "f.py").write_text(content, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "f.py")
    _git(tmp_path, "commit", "-q", "-m", "init")


def test_staged_scope_reads_index_not_working_tree(tmp_path) -> None:
    # stage a signature change, then REVERT the working tree to the HEAD content.
    _init(tmp_path, "def f(x):\n    return x\n")
    (tmp_path / "f.py").write_text("def f(x, y):\n    return x\n", encoding="utf-8")
    _git(tmp_path, "add", "f.py")  # index now has the signature change
    (tmp_path / "f.py").write_text("def f(x):\n    return x\n", encoding="utf-8")  # working tree == HEAD

    summary = GitChangeVerifier().actual_diff(str(tmp_path), "staged")
    # if it (wrongly) read the working tree, before==after -> no rows -> UNKNOWN.
    # reading the staged index blob -> signature change -> CONTRACT.
    assert summary.actual_max_change_kind == "CONTRACT"


def test_syntax_error_python_file_sets_reclassification_attempted(tmp_path) -> None:
    # we TRIED to classify a Python file but couldn't (syntax error) -> attempted=True, kind=UNKNOWN
    _init(tmp_path, "def f(x):\n    return x\n")
    (tmp_path / "f.py").write_text("def broken(:\n", encoding="utf-8")
    _git(tmp_path, "add", "f.py")
    summary = GitChangeVerifier().actual_diff(str(tmp_path), "staged")
    assert summary.reclassification_attempted is True
    assert summary.actual_max_change_kind == "UNKNOWN"


def test_non_python_change_does_not_set_reclassification_attempted(tmp_path) -> None:
    # no Python files changed -> nothing to classify -> attempted=False (don't escalate docs edits)
    _init(tmp_path, "def f(x):\n    return x\n")
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    summary = GitChangeVerifier().actual_diff(str(tmp_path), "staged")
    assert summary.reclassification_attempted is False
    assert summary.actual_max_change_kind == "UNKNOWN"


def test_docstring_only_change_classifies_cosmetic(tmp_path) -> None:
    # editing only a docstring must not escalate (cosmetic), not BEHAVIORAL and not UNKNOWN.
    _init(tmp_path, 'def f(x):\n    """Old docstring."""\n    return x\n')
    (tmp_path / "f.py").write_text(
        'def f(x):\n    """Brand new docstring text."""\n    return x\n', encoding="utf-8"
    )
    _git(tmp_path, "add", "f.py")
    summary = GitChangeVerifier().actual_diff(str(tmp_path), "staged")
    assert summary.actual_max_change_kind == "COSMETIC"


def test_module_level_constant_change_is_not_cosmetic(tmp_path) -> None:
    # a module-level semantic edit (no function row) must NOT be reported as cosmetic
    _init(tmp_path, "TAX_RATE = 0.1\n")
    (tmp_path / "f.py").write_text("TAX_RATE = 0.2\n", encoding="utf-8")
    _git(tmp_path, "add", "f.py")
    summary = GitChangeVerifier().actual_diff(str(tmp_path), "staged")
    assert summary.actual_max_change_kind == "BEHAVIORAL"


def test_comment_only_change_classifies_cosmetic(tmp_path) -> None:
    _init(tmp_path, "def f(x):\n    return x\n")
    (tmp_path / "f.py").write_text("def f(x):\n    # a new comment\n    return x\n", encoding="utf-8")
    _git(tmp_path, "add", "f.py")
    summary = GitChangeVerifier().actual_diff(str(tmp_path), "staged")
    assert summary.actual_max_change_kind == "COSMETIC"


def test_net_zero_benefit_delta_is_still_recorded(tmp_path) -> None:
    _init(tmp_path, "def f(x):\n    if x:\n        return 1\n    return 0\n")
    (tmp_path / "f.py").write_text(
        "def f(x):\n    if not x:\n        return 0\n    return 1\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "f.py")

    # a benefit fn that measures a NET-ZERO delta -> still RECORDED (keys present), distinct from
    # "nothing measured" ({}); this is the AD-29 signal for an edit that didn't move complexity/MI.
    v = GitChangeVerifier(complexity_delta_fn=lambda f, b, a: (0.0, 0.0))
    summary = v.actual_diff(str(tmp_path), "staged")
    assert summary.measured_benefit_deltas == {
        "complexity_delta": 0.0, "maintainability_index_delta": 0.0
    }
