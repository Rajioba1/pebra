"""Parity: the e2e RCA probe (a boundary-safe stdlib twin) must resolve the rust-code-analysis-cli
binary IDENTICALLY to production ``find_rca`` across every PEBRA_RCA_BIN state.

This test lives in ``tests/`` (import-allowed) precisely so it can import BOTH the production resolver
and the e2e helper and pin them together. The e2e tree may not ``import pebra`` (boundary discipline),
so any drift between the two copies is otherwise invisible — this is the guard that makes the copy safe.

The dir-override-without-launcher case (``test_parity_override_dir_without_launcher...``) is the one the
old inlined ``_rca_present`` got wrong: it reported the binary absent instead of falling through to PATH.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from e2e.utils import rca_probe
from pebra.core.rca_engine_paths import find_rca

_ENGINE = "rust-code-analysis-cli"


def _launcher_name() -> str:
    return f"{_ENGINE}.exe" if os.name == "nt" else _ENGINE


def _make_launcher(bindir: Path) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    f = bindir / _launcher_name()
    f.write_text("", encoding="utf-8")
    if os.name != "nt":
        f.chmod(0o755)
    return f


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    # Deterministic baseline: no override, and a guaranteed-empty PATH so shutil.which can't find a
    # real machine-installed binary. Individual tests override PATH/PEBRA_RCA_BIN as needed.
    monkeypatch.delenv("PEBRA_RCA_BIN", raising=False)
    empty = tmp_path / "empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    yield


def _assert_parity() -> None:
    assert rca_probe.find_rca() == find_rca()


def test_parity_unset_and_absent() -> None:
    _assert_parity()
    assert find_rca() is None


def test_parity_binary_on_path(tmp_path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    _make_launcher(bindir)
    monkeypatch.setenv("PATH", str(bindir))
    _assert_parity()
    assert find_rca() is not None


def test_parity_override_is_launcher_file(tmp_path, monkeypatch) -> None:
    f = _make_launcher(tmp_path / "b")
    monkeypatch.setenv("PEBRA_RCA_BIN", str(f))
    _assert_parity()
    assert find_rca() == str(f)


def test_parity_override_is_dir_with_launcher(tmp_path, monkeypatch) -> None:
    d = tmp_path / "b"
    _make_launcher(d)
    monkeypatch.setenv("PEBRA_RCA_BIN", str(d))
    _assert_parity()
    assert find_rca() is not None


def test_parity_override_dir_without_launcher_falls_through_to_path(tmp_path, monkeypatch) -> None:
    # THE divergent case: a misconfigured override dir must fall through to PATH, not report absent.
    empty_override = tmp_path / "override_empty"
    empty_override.mkdir()
    onpath = tmp_path / "bin"
    _make_launcher(onpath)
    monkeypatch.setenv("PEBRA_RCA_BIN", str(empty_override))
    monkeypatch.setenv("PATH", str(onpath))
    _assert_parity()
    assert find_rca() is not None  # production resolves it on PATH


def test_parity_bogus_override_falls_through_to_path(tmp_path, monkeypatch) -> None:
    onpath = tmp_path / "bin"
    _make_launcher(onpath)
    monkeypatch.setenv("PEBRA_RCA_BIN", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("PATH", str(onpath))
    _assert_parity()
    assert find_rca() is not None
