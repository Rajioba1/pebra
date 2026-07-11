"""find_rca — locate rust-code-analysis-cli via PEBRA_RCA_BIN -> PATH (2-tier: no managed-install
tier, unlike find_engine). Pure; mocks env/which. rust-code-analysis-cli is a native binary (.exe on
Windows, bare on POSIX) — NOT a .cmd shim, so no cmd-wrapping concern here."""

from __future__ import annotations

from pebra.core import rca_engine_paths as rp


def _no_env(mp):
    mp.delenv("PEBRA_RCA_BIN", raising=False)


def _posix(mp):
    mp.setattr(rp, "_is_windows", lambda: False)


def test_env_file_found(tmp_path, monkeypatch) -> None:
    f = tmp_path / "rust-code-analysis-cli"
    f.write_text("x")
    monkeypatch.setenv("PEBRA_RCA_BIN", str(f))
    assert rp.find_rca() == str(f)


def test_env_dir_finds_posix_launcher(tmp_path, monkeypatch) -> None:
    _posix(monkeypatch)
    (tmp_path / "rust-code-analysis-cli").write_text("x")
    monkeypatch.setenv("PEBRA_RCA_BIN", str(tmp_path))
    assert rp.find_rca() == str(tmp_path / "rust-code-analysis-cli")


def test_env_dir_finds_exe_on_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rp, "_is_windows", lambda: True)
    (tmp_path / "rust-code-analysis-cli.exe").write_text("x")
    monkeypatch.setenv("PEBRA_RCA_BIN", str(tmp_path))
    assert rp.find_rca() == str(tmp_path / "rust-code-analysis-cli.exe")


def test_env_dir_without_launcher_falls_through(tmp_path, monkeypatch) -> None:
    _posix(monkeypatch)
    monkeypatch.setenv("PEBRA_RCA_BIN", str(tmp_path))  # empty dir
    monkeypatch.setattr(rp.shutil, "which", lambda n: "/usr/bin/rust-code-analysis-cli")
    assert rp.find_rca() == "/usr/bin/rust-code-analysis-cli"


def test_env_nonexistent_falls_through(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PEBRA_RCA_BIN", str(tmp_path / "does_not_exist"))
    monkeypatch.setattr(rp.shutil, "which", lambda n: "/usr/bin/rust-code-analysis-cli")
    assert rp.find_rca() == "/usr/bin/rust-code-analysis-cli"


def test_env_empty_string_falls_through(monkeypatch) -> None:
    monkeypatch.setenv("PEBRA_RCA_BIN", "")
    monkeypatch.setattr(rp.shutil, "which", lambda n: "/usr/bin/rca")
    assert rp.find_rca() == "/usr/bin/rca"


def test_which_hit_when_env_absent(monkeypatch) -> None:
    _no_env(monkeypatch)
    monkeypatch.setattr(rp.shutil, "which", lambda n: "/usr/bin/rca")
    assert rp.find_rca() == "/usr/bin/rca"


def test_env_file_wins_over_path(tmp_path, monkeypatch) -> None:
    f = tmp_path / "rca"
    f.write_text("x")
    monkeypatch.setenv("PEBRA_RCA_BIN", str(f))
    monkeypatch.setattr(rp.shutil, "which", lambda n: "/usr/bin/rca")
    assert rp.find_rca() == str(f)


def test_all_miss_returns_none(monkeypatch) -> None:
    _no_env(monkeypatch)
    monkeypatch.setattr(rp.shutil, "which", lambda n: None)
    assert rp.find_rca() is None


def test_rca_source_and_runtime_are_pinned() -> None:
    assert rp.RCA_ACCEPTED_VERSION == "0.0.25"
    assert rp.RCA_SOURCE_REVISION == "37e5d83c056c8cbf827223d5814a93c5218df1a9"
