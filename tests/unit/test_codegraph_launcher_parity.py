"""Parity: the e2e codegraph-launcher resolver (used by graph_resolver.find_codegraph_db as its
best-effort fallback) must resolve the codegraph launcher IDENTICALLY to production ``find_engine``:
PEBRA_CODEGRAPH_BIN override (file/dir) -> PATH -> pinned managed install. The e2e tree may not
``import pebra`` (boundary discipline), so this tests/-side parity check is what keeps the boundary-safe
twin from drifting.

The managed-install tier uses a copied pinned-version literal in e2e; this test imports production's
``CODEGRAPH_DEFAULT_VERSION`` and proves the e2e resolver ignores stale non-default managed installs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from e2e.external.utils.graph_resolver import _resolve_codegraph_launcher
from pebra.core.engine_paths import find_engine
from pebra.core.graph_version import CODEGRAPH_DEFAULT_VERSION

_ENGINE = "codegraph"


def _launcher_name() -> str:
    return f"{_ENGINE}.cmd" if os.name == "nt" else _ENGINE


def _make_launcher(bindir: Path) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    f = bindir / _launcher_name()
    f.write_text("", encoding="utf-8")
    if os.name != "nt":
        f.chmod(0o755)
    return f


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PEBRA_CODEGRAPH_BIN", raising=False)
    empty = tmp_path / "empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    yield


def test_parity_override_is_launcher_file(tmp_path, monkeypatch) -> None:
    f = _make_launcher(tmp_path / "b")
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(f))
    assert _resolve_codegraph_launcher() == find_engine() == str(f)


def test_parity_override_is_dir_with_launcher(tmp_path, monkeypatch) -> None:
    d = tmp_path / "b"
    _make_launcher(d)
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(d))
    resolved = _resolve_codegraph_launcher()
    assert resolved == find_engine()
    assert resolved is not None


def test_parity_binary_on_path(tmp_path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    _make_launcher(bindir)
    monkeypatch.setenv("PATH", str(bindir))
    resolved = _resolve_codegraph_launcher()
    assert resolved == find_engine()
    assert resolved is not None


def test_parity_bogus_override_falls_through_to_path(tmp_path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    _make_launcher(bindir)
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("PATH", str(bindir))
    resolved = _resolve_codegraph_launcher()
    assert resolved == find_engine()
    assert resolved is not None


def test_parity_managed_install_uses_pinned_default_version(tmp_path, monkeypatch) -> None:
    # Production only trusts the pinned managed install. The e2e twin must not scan every installed
    # version, or a stale managed codegraph can be used in an assay while production ignores it.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    stale = tmp_path / ".codegraph" / "pebra" / "0.0.0" / "bin"
    pinned = tmp_path / ".codegraph" / "pebra" / CODEGRAPH_DEFAULT_VERSION / "bin"
    stale_launcher = _make_launcher(stale)
    pinned_launcher = _make_launcher(pinned)

    assert _resolve_codegraph_launcher() == find_engine() == str(pinned_launcher)
    assert _resolve_codegraph_launcher() != str(stale_launcher)
