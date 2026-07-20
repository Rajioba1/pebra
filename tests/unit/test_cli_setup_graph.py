"""M5c.5 — `pebra setup-graph` / `pebra doctor`: version policy, OS/arch detection, checksum-verified
standalone install, pinned npm fallback, and orchestration. All mocked — no network, no real binary."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
import zipfile

from pebra.cli import setup_graph as sg
from pebra.core.graph_version import CODEGRAPH_DEFAULT_VERSION

_FRESH = {"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
          "index": {"reindexRecommended": False}}
_MISMATCH = {"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
             "index": {"reindexRecommended": False},
             "worktreeMismatch": {"worktreeRoot": "/wt", "indexRoot": "/main"}}


def _args(**kw):
    base = {"repo_root": "/repo", "as_json": False, "fix": False, "fix_graph": False,
            "version": None, "allow_unsupported": False, "via": "auto"}
    base.update(kw)
    return argparse.Namespace(**base)


def _make_targz(top: str, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_zip(top: str, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(f"{top}/{name}", data)
    return buf.getvalue()


# --- version policy ---

def test_resolve_version_default_is_pinned() -> None:
    assert sg._resolve_version(None, False, False) == (CODEGRAPH_DEFAULT_VERSION, None)


def test_resolve_version_in_range_ok() -> None:
    assert sg._resolve_version("1.1.7", False, False) == ("1.1.7", None)


def test_resolve_version_out_of_range_refused() -> None:
    v, err = sg._resolve_version("1.2.0", False, True)
    assert v is None and err == 2


def test_resolve_version_out_of_range_allowed_with_flag() -> None:
    assert sg._resolve_version("1.2.0", True, False) == ("1.2.0", None)


def test_run_setup_graph_refuses_out_of_range_version_without_install(monkeypatch) -> None:
    attempted = []
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: attempted.append(1))
    assert sg.run_setup_graph(_args(version="9.9.9")) == 2
    assert attempted == []  # refused before any install attempt


# --- platform detection ---

def test_target_supported(monkeypatch) -> None:
    monkeypatch.setattr(sg.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sg.platform, "machine", lambda: "arm64")
    assert sg._target() == "darwin-arm64"


def test_target_unsupported_arch(monkeypatch) -> None:
    monkeypatch.setattr(sg.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sg.platform, "machine", lambda: "s390x")
    assert sg._target() is None


def test_is_musl_false_off_linux(monkeypatch) -> None:
    monkeypatch.setattr(sg.platform, "system", lambda: "Darwin")
    assert sg._is_musl() is False


# --- checksum parse + extraction ---

def test_expected_sha_basename_match() -> None:
    text = "aa11  codegraph-linux-x64.tar.gz\nbb22  SHA256SUMS\n"
    assert sg._expected_sha(text, "codegraph-linux-x64.tar.gz") == "aa11"
    assert sg._expected_sha(text, "nope") is None


def test_extract_strips_top_dir_and_blocks_traversal(tmp_path) -> None:
    data = _make_targz("codegraph-darwin-arm64",
                       {"codegraph": b"#!/bin/sh\n", "lib/x": b"y", "../evil": b"z"})
    dest = tmp_path / "d"
    sg._extract(data, "codegraph-darwin-arm64.tar.gz", dest)
    assert (dest / "codegraph").read_bytes() == b"#!/bin/sh\n"  # top dir stripped
    assert (dest / "lib" / "x").read_bytes() == b"y"
    assert not (tmp_path / "evil").exists()  # traversal entry skipped


def test_extract_zip_strips_top_dir_and_blocks_traversal(tmp_path) -> None:
    # the .zip branch is the Windows path — exercise top-dir strip + zip-slip guard separately
    data = _make_zip("codegraph-win32-x64",
                     {"codegraph.exe": b"MZ", "lib/x": b"y", "../evil": b"z"})
    dest = tmp_path / "d"
    sg._extract(data, "codegraph-win32-x64.zip", dest)
    assert (dest / "codegraph.exe").read_bytes() == b"MZ"
    assert (dest / "lib" / "x").read_bytes() == b"y"
    assert not (tmp_path / "evil").exists()


# --- standalone install ---

def test_install_standalone_verifies_checksum_and_installs(tmp_path, monkeypatch) -> None:
    asset = _make_targz("codegraph-darwin-arm64", {"bin/codegraph": b"#!/bin/sh\n"})
    sums = f"{hashlib.sha256(asset).hexdigest()}  codegraph-darwin-arm64.tar.gz\n"
    urls = []
    monkeypatch.setattr(sg, "_download",
                        lambda url, timeout=60: (urls.append(url), sums.encode()
                                                 if url.endswith("SHA256SUMS") else asset)[1])
    monkeypatch.setattr(sg, "_install_root", lambda v: tmp_path / "inst")
    monkeypatch.setattr(sg, "_run", lambda cmd, timeout: (0, "1.1.1", ""))  # launcher --version
    monkeypatch.setattr(sg, "_link_onto_path", lambda *a, **k: None)
    assert sg._install_standalone(True, "1.1.1", "darwin-arm64") is True
    assert (tmp_path / "inst" / "bin" / "codegraph").exists()
    assert all("/v1.1.1/" in url for url in urls)  # GitHub release tag, not bare package version


def test_install_standalone_rejects_checksum_mismatch(tmp_path, monkeypatch) -> None:
    asset = _make_targz("codegraph-darwin-arm64", {"codegraph": b"x"})
    sums = "deadbeef  codegraph-darwin-arm64.tar.gz\n"
    monkeypatch.setattr(sg, "_download",
                        lambda url, timeout=60: sums.encode() if url.endswith("SHA256SUMS") else asset)
    monkeypatch.setattr(sg, "_install_root", lambda v: tmp_path / "inst")
    ran = []
    monkeypatch.setattr(sg, "_run", lambda cmd, timeout: (ran.append(cmd), (0, "", ""))[1])
    assert sg._install_standalone(True, "1.1.1", "darwin-arm64") is False
    assert ran == []  # never executed an unverified binary
    assert not (tmp_path / "inst").exists()  # never extracted


# --- npm fallback ---

def test_install_npm_uses_pinned_spec(monkeypatch) -> None:
    monkeypatch.setattr(sg.shutil, "which", lambda n: "/usr/bin/" + n)
    calls = []
    monkeypatch.setattr(sg, "_run", lambda cmd, timeout: (calls.append(cmd), (0, "", ""))[1])
    monkeypatch.setattr(sg, "_installed", lambda: True)
    assert sg._install_npm(True, "1.1.1") is None
    assert calls[0] == ["npm", "install", "-g", "@colbymchenry/codegraph@1.1.1"]  # PINNED


def test_install_npm_absent_fails_clear(monkeypatch) -> None:
    monkeypatch.setattr(sg.shutil, "which", lambda n: None)
    assert sg._install_npm(True, "1.1.1") == 1


def test_ensure_installed_standalone_then_npm_fallback(monkeypatch) -> None:
    # standalone fails -> auto falls back to npm (pinned)
    monkeypatch.setattr(sg, "_installed", lambda: False)
    monkeypatch.setattr(sg, "_target", lambda: "linux-x64")
    monkeypatch.setattr(sg, "_is_musl", lambda: False)
    monkeypatch.setattr(sg, "_install_standalone", lambda *a, **k: False)
    npm = []
    monkeypatch.setattr(sg, "_install_npm", lambda aj, v: (npm.append(v), None)[1])
    assert sg._ensure_installed(True, version="1.1.1", via="auto", explicit_version=False) is None
    assert npm == ["1.1.1"]


def test_ensure_installed_musl_fails_clear_when_standalone_only(monkeypatch) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: False)
    monkeypatch.setattr(sg, "_target", lambda: None)
    monkeypatch.setattr(sg, "_is_musl", lambda: True)
    assert sg._ensure_installed(True, version="1.1.1", via="standalone", explicit_version=False) == 1


def test_ensure_installed_auto_skips_reinstall_when_in_range(monkeypatch) -> None:
    # already-present + in-range binary under via=auto -> no reinstall (neither route called)
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")
    called: list[str] = []
    monkeypatch.setattr(sg, "_install_standalone", lambda *a, **k: called.append("standalone") or True)
    monkeypatch.setattr(sg, "_install_npm", lambda *a, **k: called.append("npm") or None)
    assert sg._ensure_installed(False, version="1.1.1", via="auto", explicit_version=False) is None
    assert called == []


def test_ensure_installed_explicit_version_does_not_skip_different_current(monkeypatch) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(sg, "_target", lambda: "linux-x64")
    monkeypatch.setattr(sg, "_is_musl", lambda: False)
    called: list[str] = []
    monkeypatch.setattr(sg, "_install_standalone", lambda aj, v, t: called.append(v) or True)
    assert sg._ensure_installed(
        False, version="2.0.0", via="auto", explicit_version=True
    ) is None
    assert called == ["2.0.0"]


def test_link_onto_path_updates_current_process_path_for_immediate_init(tmp_path, monkeypatch) -> None:
    # Windows: the real launcher is .cmd; in-process PATH is updated so the same process can run init/sync.
    monkeypatch.setattr(sg.platform, "system", lambda: "Windows")
    monkeypatch.setenv("PATH", "OLDPATH")
    launcher = tmp_path / "inst" / "bin" / "codegraph.cmd"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"")
    sg._link_onto_path(launcher, True, "1.1.1")
    assert str(launcher.parent) in sg.os.environ["PATH"].split(sg.os.pathsep)


def test_install_standalone_win32_uses_cmd_launcher(tmp_path, monkeypatch) -> None:
    # the win32 bundle's launcher is bin/codegraph.cmd (no .exe) — verify must run THAT (A2 finding)
    asset = _make_zip("codegraph-win32-x64", {"bin/codegraph.cmd": b"@echo 1.1.1\n", "node.exe": b"x"})
    sums = f"{hashlib.sha256(asset).hexdigest()}  codegraph-win32-x64.zip\n"
    monkeypatch.setattr(sg, "_download",
                        lambda url, timeout=60: sums.encode() if url.endswith("SHA256SUMS") else asset)
    monkeypatch.setattr(sg, "_install_root", lambda v: tmp_path / "inst")
    verified: list[list[str]] = []
    monkeypatch.setattr(sg, "_run", lambda cmd, timeout: (verified.append(cmd), (0, "1.1.1", ""))[1])
    monkeypatch.setattr(sg, "_link_onto_path", lambda *a, **k: None)
    assert sg._install_standalone(True, "1.1.1", "win32-x64") is True
    assert (tmp_path / "inst" / "bin" / "codegraph.cmd").exists()
    assert verified and verified[0][0].endswith("codegraph.cmd")  # verified the .cmd, not a .exe


def test_allow_unsupported_install_is_still_untrusted_at_runtime() -> None:
    # the ratified invariant: install-allowed != trusted-at-runtime. --allow-unsupported permits
    # installing an out-of-range version, but the adapter's runtime range check still marks it untrusted.
    from pebra.adapters import codegraph_adapter as cga
    from pebra.core.models import CandidateAction

    # install side: out-of-range version is permitted with the flag
    assert sg._resolve_version("2.0.0", True, False) == ("2.0.0", None)

    # runtime side: that same running version is untrusted -> unresolved (feeds Gate 13)
    status = {"initialized": True, "version": "2.0.0",
              "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
              "index": {"reindexRecommended": False}}
    ev = cga.CodeGraphAdapter(status_fn=lambda r: status).fanin(
        CandidateAction(id="a1", label="p", action_type="edit"), "/repo")
    assert ev.resolution_method == "unresolved"
    assert "outside the accepted range" in (ev.fallback_reason or "")


# --- orchestration (install mocked) ---

class _Engine:
    def __init__(self, status_payloads, version="1.1.1"):
        self._status = list(status_payloads)
        self._version = version
        self.calls = []

    def __call__(self, cmd, timeout):
        self.calls.append(cmd)
        # key on the SUBCOMMAND (cmd[1]), not cmd[0]: cmd[0] is the resolved engine path (a real
        # managed install on a dev box, or bare "codegraph") — the subcommand is invariant.
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "status":
            p = self._status.pop(0) if self._status else None
            return (1, "", "") if p is None else (0, json.dumps(p), "")
        if sub == "--version":
            return (0, self._version, "")
        return (0, "", "")  # init / sync


def test_setup_graph_happy_builds_index(monkeypatch) -> None:
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)
    eng = _Engine([_FRESH])
    monkeypatch.setattr(sg, "_run", eng)
    assert sg.run_setup_graph(_args()) == 0
    assert any(c[1:] == ["init", "/repo"] for c in eng.calls)  # init ran (cmd[0] = resolved exe)


def test_setup_graph_fix_nonzero_if_mismatch_persists(monkeypatch) -> None:
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)
    monkeypatch.setattr(sg, "_run", _Engine([_MISMATCH]))
    assert sg.run_setup_graph(_args(fix=True)) == 1


def test_doctor_reports_version_and_range(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")  # version detection mocked
    monkeypatch.setattr(sg, "_run", _Engine([_FRESH]))
    rc = sg.run_doctor(_args(as_json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["version_in_range"] is True
    assert payload["codegraph_version"] == "1.1.1"


def test_doctor_out_of_range_is_unhealthy(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "2.0.0")
    monkeypatch.setattr(sg, "_run", _Engine([_FRESH]))
    rc = sg.run_doctor(_args(as_json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1 and payload["version_in_range"] is False


def test_doctor_absent_engine_fails_clear(monkeypatch) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: False)
    assert sg.run_doctor(_args()) == 1
