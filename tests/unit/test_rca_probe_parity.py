"""Parity: the e2e RCA probe (a boundary-safe stdlib twin) must resolve the rust-code-analysis-cli
binary IDENTICALLY to production ``find_rca`` across every PEBRA_RCA_BIN state.

This test lives in ``tests/`` (import-allowed) precisely so it can import BOTH the production resolver
and the e2e helper and pin them together. The e2e tree may not ``import pebra`` (boundary discipline),
so any drift between the two copies is otherwise invisible — this is the guard that makes the copy safe.

The dir-override-without-launcher case (``test_parity_override_dir_without_launcher...``) is the one the
old inlined ``_rca_present`` got wrong: it reported the binary absent instead of falling through to PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from e2e.utils import rca_probe
from pebra.core.rca_engine_paths import RCA_ACCEPTED_VERSION, RCA_SOURCE_REVISION, find_rca

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


def test_agent_assay_config_pins_the_production_rca_toolchain() -> None:
    config_path = Path(__file__).resolve().parents[2] / "e2e" / "experiments" / "agent_ab" / "config.json"
    toolchain = json.loads(config_path.read_text(encoding="utf-8"))["toolchain"]["rca"]

    assert toolchain["version"] == RCA_ACCEPTED_VERSION
    assert toolchain["source_revision"] == RCA_SOURCE_REVISION


def test_e2e_rca_fingerprint_records_version_and_binary_hash(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "bin" / "rca"
    binary.parent.mkdir()
    binary.write_bytes(b"pinned binary")
    (tmp_path / ".crates2.json").write_text(json.dumps({"installs": {
        "rust-code-analysis-cli 0.0.25 (git+https://github.com/mozilla/rust-code-analysis"
        "#37e5d83c056c8cbf827223d5814a93c5218df1a9)": {"bins": [binary.name]},
    }}), encoding="utf-8")
    monkeypatch.setattr(rca_probe, "find_rca", lambda: str(binary))
    monkeypatch.setattr(
        rca_probe.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="rust-code-analysis-cli 0.0.25\n"),
    )

    result = rca_probe.fingerprint(
        accepted_version=RCA_ACCEPTED_VERSION,
        required_source_revision=RCA_SOURCE_REVISION,
    )

    assert result["status"] == "accepted"
    assert result["validation_mode"] == "cargo_revision"
    assert result["version"] == "0.0.25"
    assert result["sha256"] == "c1dd1e3ff1ec0ea985242912037b4477b10a600d8e1c252eadf4c697120800f5"


def test_explicit_hash_mismatch_overrides_matching_cargo_revision(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "bin" / "rca"
    binary.parent.mkdir()
    binary.write_bytes(b"actual binary")
    (tmp_path / ".crates2.json").write_text(json.dumps({"installs": {
        "rust-code-analysis-cli 0.0.25 (git+https://github.com/mozilla/rust-code-analysis"
        "#37e5d83c056c8cbf827223d5814a93c5218df1a9)": {"bins": [binary.name]},
    }}), encoding="utf-8")
    monkeypatch.setenv("PEBRA_RCA_SHA256", "0" * 64)
    monkeypatch.setattr(rca_probe, "find_rca", lambda: str(binary))
    monkeypatch.setattr(
        rca_probe.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, stdout="rust-code-analysis-cli 0.0.25\n"
        ),
    )

    result = rca_probe.fingerprint(
        accepted_version=RCA_ACCEPTED_VERSION,
        required_source_revision=RCA_SOURCE_REVISION,
    )

    assert result["status"] == "rejected"
    assert result["validation_mode"] is None


def test_e2e_rca_fingerprint_distinguishes_absence_from_probe_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rca_probe, "find_rca", lambda: None)
    absent = rca_probe.fingerprint(
        accepted_version=RCA_ACCEPTED_VERSION,
        required_source_revision=RCA_SOURCE_REVISION,
    )
    assert absent["status"] == "absent"

    binary = tmp_path / "rca"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(rca_probe, "find_rca", lambda: str(binary))
    monkeypatch.setattr(
        rca_probe.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("rca", 10)),
    )
    failed = rca_probe.fingerprint(
        accepted_version=RCA_ACCEPTED_VERSION,
        required_source_revision=RCA_SOURCE_REVISION,
    )
    assert failed["status"] == "probe_error"
    assert failed["sha256"] is not None
