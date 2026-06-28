"""A2 follow-up — find_engine: locate codegraph via PEBRA_CODEGRAPH_BIN -> PATH -> managed install,
so `setup-graph` makes PEBRA ready without a persistent PATH edit. Pure; mocks env/which/managed root."""

from __future__ import annotations

from pebra.core import engine_paths as ep
from pebra.core.graph_version import CODEGRAPH_DEFAULT_VERSION


def _no_env(monkeypatch):
    monkeypatch.delenv("PEBRA_CODEGRAPH_BIN", raising=False)


def _posix(monkeypatch):
    monkeypatch.setattr(ep, "_is_windows", lambda: False)


# --- managed root is the single source of truth (must match setup_graph's install location) ---

def test_managed_install_root_convention() -> None:
    from pathlib import Path
    assert ep.managed_install_root("1.1.1") == Path.home() / ".codegraph" / "pebra" / "1.1.1"


# --- env override ---

def test_env_file_found(tmp_path, monkeypatch) -> None:
    f = tmp_path / "codegraph"
    f.write_text("x")
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(f))
    assert ep.find_engine() == str(f)


def test_env_dir_finds_posix_launcher(tmp_path, monkeypatch) -> None:
    _posix(monkeypatch)
    (tmp_path / "codegraph").write_text("x")
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(tmp_path))
    assert ep.find_engine() == str(tmp_path / "codegraph")


def test_env_dir_finds_cmd_launcher_on_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ep, "_is_windows", lambda: True)
    (tmp_path / "codegraph.cmd").write_text("x")
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(tmp_path))
    assert ep.find_engine() == str(tmp_path / "codegraph.cmd")


def test_env_dir_without_launcher_falls_through(tmp_path, monkeypatch) -> None:
    _posix(monkeypatch)
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(tmp_path))  # empty dir
    monkeypatch.setattr(ep.shutil, "which", lambda n: "/usr/bin/codegraph")
    monkeypatch.setattr(ep, "managed_install_root", lambda v: tmp_path / "nope")
    assert ep.find_engine() == "/usr/bin/codegraph"  # fell through to PATH


def test_env_nonexistent_falls_through(tmp_path, monkeypatch) -> None:
    _posix(monkeypatch)
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(tmp_path / "does_not_exist"))
    monkeypatch.setattr(ep.shutil, "which", lambda n: "/usr/bin/codegraph")
    assert ep.find_engine() == "/usr/bin/codegraph"  # no crash on bad override


def test_env_empty_string_falls_through(monkeypatch) -> None:
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", "")
    monkeypatch.setattr(ep.shutil, "which", lambda n: "/usr/bin/codegraph")
    assert ep.find_engine() == "/usr/bin/codegraph"


# --- PATH ---

def test_which_hit_when_env_absent(monkeypatch) -> None:
    _no_env(monkeypatch)
    monkeypatch.setattr(ep.shutil, "which", lambda n: "/usr/bin/codegraph")
    assert ep.find_engine() == "/usr/bin/codegraph"


def test_env_file_wins_over_path(tmp_path, monkeypatch) -> None:
    f = tmp_path / "codegraph"
    f.write_text("x")
    monkeypatch.setenv("PEBRA_CODEGRAPH_BIN", str(f))
    monkeypatch.setattr(ep.shutil, "which", lambda n: "/usr/bin/codegraph")
    assert ep.find_engine() == str(f)  # env beats PATH


# --- managed install fallback ---

def test_managed_hit_when_path_misses(tmp_path, monkeypatch) -> None:
    _no_env(monkeypatch)
    _posix(monkeypatch)
    monkeypatch.setattr(ep.shutil, "which", lambda n: None)
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "codegraph").write_text("x")
    monkeypatch.setattr(ep, "managed_install_root", lambda v: root)
    assert ep.find_engine() == str(root / "bin" / "codegraph")


def test_all_miss_returns_none(tmp_path, monkeypatch) -> None:
    _no_env(monkeypatch)
    _posix(monkeypatch)
    monkeypatch.setattr(ep.shutil, "which", lambda n: None)
    monkeypatch.setattr(ep, "managed_install_root", lambda v: tmp_path / "absent")
    assert ep.find_engine() is None
