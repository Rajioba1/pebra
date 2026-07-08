"""Shared adapter path safety — adapters that READ caller-supplied file paths (RCA 4b, bandit 4c)
must reject paths that escape the repo (absolute, ``..`` traversal, or symlink-resolving-outside)
BEFORE any read/copy. Invalid paths are dropped, never raised; the caller degrades to projected.
"""

from __future__ import annotations

from pebra.adapters._paths import safe_relative_files


def test_keeps_valid_relative_paths(tmp_path) -> None:
    assert safe_relative_files(str(tmp_path), ["src/m.py", "a.py"]) == ["src/m.py", "a.py"]


def test_rejects_parent_traversal(tmp_path) -> None:
    assert safe_relative_files(str(tmp_path), ["../outside.py"]) == []
    assert safe_relative_files(str(tmp_path), ["src/../../escape.py"]) == []


def test_rejects_absolute_paths(tmp_path) -> None:
    abs_inside = str((tmp_path / "m.py").resolve())
    assert safe_relative_files(str(tmp_path), [abs_inside]) == []  # absolute rejected even if inside


def test_filters_mixed_keeping_only_safe(tmp_path) -> None:
    assert safe_relative_files(str(tmp_path), ["../bad.py", "ok.py"]) == ["ok.py"]


def test_empty_input_is_empty(tmp_path) -> None:
    assert safe_relative_files(str(tmp_path), []) == []
