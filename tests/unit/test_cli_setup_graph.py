"""M5c.5 — `pebra setup-graph` / `pebra doctor`: version policy, OS/arch detection, checksum-verified
standalone install, pinned npm fallback, and orchestration. All mocked — no network, no real binary."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
import zipfile
from types import SimpleNamespace

import pytest

from pebra.cli import setup_graph as sg
from pebra.core import engine_argv as ea
from pebra.core.graph_version import CODEGRAPH_DEFAULT_VERSION
from pebra.core.graph_snapshot import GraphSnapshot

_FRESH = {"initialized": True, "version": "1.1.1",
          "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
          "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
          "worktreeMismatch": None}
_MISMATCH = {"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
             "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
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


def test_install_npm_windows_uses_direct_node_argv(tmp_path, monkeypatch) -> None:
    launcher = tmp_path / "nodejs" / "npm.cmd"
    node = launcher.parent / "node.exe"
    npm_cli = launcher.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True)
    launcher.write_text("shim", encoding="utf-8")
    node.write_bytes(b"node")
    npm_cli.write_text("script", encoding="utf-8")
    monkeypatch.setattr(
        sg,
        "resolve_engine_argv",
        lambda exe, args: ea._resolve_engine_argv(exe, args, os_name="nt"),
    )
    monkeypatch.setattr(
        sg.shutil,
        "which",
        lambda name: str(launcher) if name == "npm" else str(node) if name == "node" else None,
    )
    calls: list[list[str]] = []

    def bounded(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0, stdout="", stderr="", error=None, stdout_truncated=False
        )

    monkeypatch.setattr(sg, "run_bounded", bounded)
    monkeypatch.setattr(sg, "_installed", lambda: True)

    assert sg._install_npm(True, "1.1.1") is None
    assert calls == [[
        str(node), str(npm_cli), "install", "-g", "@colbymchenry/codegraph@1.1.1",
    ]]


def test_run_never_leaks_raw_launcher_exception(monkeypatch) -> None:
    def fail(_exe, _args):
        raise OSError(r"private C:\repo setup-graph --fix query")

    monkeypatch.setattr(sg, "resolve_engine_argv", fail)

    rc, out, error = sg._run(["codegraph", "status", "private-query"], timeout=1)

    assert rc == 127
    assert out == ""
    assert error == "process launch failed"


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


def test_ensure_installed_via_npm_never_attempts_standalone(monkeypatch) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: False)
    called: list[str] = []
    monkeypatch.setattr(
        sg, "_install_standalone", lambda *a, **k: called.append("standalone") or True
    )
    monkeypatch.setattr(sg, "_install_npm", lambda *a, **k: called.append("npm") or None)

    assert sg._ensure_installed(
        True, version="1.1.1", via="npm", explicit_version=False
    ) is None
    assert called == ["npm"]


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
              "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
              "worktreeMismatch": None}
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
    eng = _Engine([])
    monkeypatch.setattr(sg, "_run", eng)

    class Adapter:
        def prepare(self, _repo_root):
            return GraphSnapshot(
                status="available", provider="CodeGraph", provider_version="1.1.1",
                index_version="24", repo_head="commit", config_digest="absent",
                graph_scope_digest="scope", sync_performed=True, fallback_reason=None,
            )

        def prepared_status(self, _repo_root):
            return _FRESH

    monkeypatch.setattr(sg, "CodeGraphAdapter", Adapter)
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


@pytest.mark.parametrize(
    "malformed",
    [
        [],
        {**_FRESH, "pendingChanges": {"added": True, "modified": 0, "removed": 0}},
        {**_FRESH, "pendingChanges": {"added": 0, "modified": 0}},
        {**_FRESH, "index": {"reindexRecommended": False}},
        {**_FRESH, "index": {"reindexRecommended": 0, "builtWithExtractionVersion": 24}},
    ],
)
def test_doctor_rejects_malformed_status_without_crash_or_partial_health(
    malformed, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(sg, "_run", _Engine([malformed]))

    assert sg.run_doctor(_args(as_json=True)) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnosis"]["healthy"] is False
    assert payload["diagnosis"]["detail"] == "codegraph status malformed"


def test_doctor_absent_engine_fails_clear(monkeypatch) -> None:
    monkeypatch.setattr(sg, "_installed", lambda: False)
    assert sg.run_doctor(_args()) == 1


def test_doctor_reports_config_even_when_engine_is_absent(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "codegraph.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sg, "_installed", lambda: False)

    assert sg.run_doctor(_args(repo_root=str(tmp_path), as_json=True)) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["graph_config"]["exists"] is True
    assert payload["graph_config"]["digest"] == hashlib.sha256(b"{}").hexdigest()


def test_graph_config_reports_supported_fields_and_exclude_as_unsupported(tmp_path) -> None:
    raw = json.dumps({
        "extensions": {".extx": "python"},
        "includeIgnored": ["vendor/**"],
        "exclude": ["generated/**"],
    }).encode()
    (tmp_path / "codegraph.json").write_bytes(raw)

    config = sg._graph_config(str(tmp_path))

    assert config == {
        "state": "readable",
        "exists": True,
        "digest": hashlib.sha256(raw).hexdigest(),
        "valid": True,
        "extensions": {".extx": "python"},
        "include_ignored": ["vendor/**"],
        "supported_fields": ["extensions", "includeIgnored"],
        "unsupported_fields": ["exclude"],
        "error": None,
    }


def test_graph_config_reports_absent_and_malformed_without_repair(tmp_path) -> None:
    assert sg._graph_config(str(tmp_path))["state"] == "absent"
    malformed = tmp_path / "codegraph.json"
    malformed.write_bytes(b"{not json")

    config = sg._graph_config(str(tmp_path))

    assert config["exists"] is True
    assert config["valid"] is False
    assert config["error"] == "malformed JSON"
    assert malformed.read_bytes() == b"{not json"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"number":' + (b"9" * 5_000) + b"}",
        (b"[" * 2_000) + b"0" + (b"]" * 2_000),
    ],
    ids=("oversized-integer", "deep-nesting"),
)
def test_graph_config_pathological_json_is_malformed_not_exception(tmp_path, raw) -> None:
    (tmp_path / "codegraph.json").write_bytes(raw)

    config = sg._graph_config(str(tmp_path))

    assert config["valid"] is False
    assert config["error"] == "malformed JSON"


@pytest.mark.parametrize(
    "raw",
    [
        '{"number":' + ("9" * 5_000) + "}",
        ("[" * 2_000) + "0" + ("]" * 2_000),
    ],
    ids=("oversized-integer", "deep-nesting"),
)
def test_status_pathological_json_fails_soft(monkeypatch, raw) -> None:
    monkeypatch.setattr(sg, "_run", lambda *_args, **_kwargs: (0, raw, ""))

    assert sg._status("/repo") is None


def test_graph_config_distinguishes_nonregular_state(tmp_path) -> None:
    (tmp_path / "codegraph.json").mkdir()

    config = sg._graph_config(str(tmp_path))

    assert config["state"] == "nonregular"
    assert config["valid"] is False
    assert config["error"] == "codegraph configuration is not a regular file"


def test_setup_rejects_unreadable_config_before_install_or_init(tmp_path, monkeypatch) -> None:
    config = tmp_path / "codegraph.json"
    config.write_bytes(b"{}")
    original_read = sg.Path.read_bytes

    def unreadable(path):
        if path == config:
            raise PermissionError("private path")
        return original_read(path)

    monkeypatch.setattr(sg.Path, "read_bytes", unreadable)
    calls: list[str] = []
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: calls.append("install"))
    monkeypatch.setattr(sg, "_initialize_worktree_local_index", lambda _root: calls.append("init"))

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 1
    assert calls == []


def test_setup_rejects_nonregular_config_before_install_or_init(tmp_path, monkeypatch) -> None:
    (tmp_path / "codegraph.json").mkdir()
    calls: list[str] = []
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: calls.append("install"))
    monkeypatch.setattr(sg, "_initialize_worktree_local_index", lambda _root: calls.append("init"))

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 1
    assert calls == []


def test_setup_atomically_restores_provider_mutation_of_existing_config(
    tmp_path, monkeypatch, capsys
) -> None:
    raw = b'{"includeIgnored":["vendor/**"]}\n'
    config = tmp_path / "codegraph.json"
    config.write_bytes(raw)
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)

    def mutate(_root):
        config.write_bytes(b'{"provider":"changed"}')
        return True

    monkeypatch.setattr(sg, "_initialize_worktree_local_index", mutate)
    monkeypatch.setattr(sg, "_prepare_worktree_local_index", lambda *_args: _FRESH)

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert config.read_bytes() == raw
    assert payload["config_restored"] is True
    assert payload["graph_config"]["digest"] == hashlib.sha256(raw).hexdigest()


def test_setup_removes_provider_created_config_when_initially_absent(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "codegraph.json"
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)

    def create(_root):
        config.write_bytes(b"{}")
        return True

    monkeypatch.setattr(sg, "_initialize_worktree_local_index", create)
    monkeypatch.setattr(sg, "_prepare_worktree_local_index", lambda *_args: _FRESH)

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 0
    assert not config.exists()


def test_setup_restore_failure_is_stable_and_unhealthy(tmp_path, monkeypatch, capsys) -> None:
    config = tmp_path / "codegraph.json"
    config.write_bytes(b"before")
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)
    monkeypatch.setattr(
        sg, "_initialize_worktree_local_index", lambda _root: config.write_bytes(b"after") or True
    )
    monkeypatch.setattr(sg, "_restore_graph_config", lambda _state: False)

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["config_restored"] is False
    assert payload["config_error"] == "codegraph configuration restore failed"


def test_setup_restores_existing_config_before_prepare_and_snapshot_uses_restored_digest(
    tmp_path, monkeypatch
) -> None:
    raw = b'{"includeIgnored":["vendor/**"]}\n'
    config = tmp_path / "codegraph.json"
    config.write_bytes(raw)
    expected_digest = hashlib.sha256(raw).hexdigest()
    calls: list[str] = []
    snapshots: list[GraphSnapshot] = []
    original_capture = sg._capture_graph_config
    original_restore = sg._restore_graph_config

    def capture(root):
        if "capture" not in calls:
            calls.append("capture")
        return original_capture(root)

    def initialize(_root):
        calls.append("init")
        config.write_bytes(b'{"provider":"mutated"}')
        return True

    def restore(state):
        calls.append("restore")
        return original_restore(state)

    class Adapter:
        def prepare(self, repo_root):
            calls.append("prepare")
            assert repo_root == str(tmp_path)
            assert config.read_bytes() == raw
            snapshot = GraphSnapshot(
                status="available", provider="CodeGraph", provider_version="1.1.1",
                index_version="24", repo_head="commit", config_digest=expected_digest,
                graph_scope_digest="scope", sync_performed=True, fallback_reason=None,
            )
            snapshots.append(snapshot)
            return snapshot

        def prepared_status(self, _repo_root):
            return _FRESH

    monkeypatch.setattr(sg, "_capture_graph_config", capture)
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)
    monkeypatch.setattr(sg, "_initialize_worktree_local_index", initialize)
    monkeypatch.setattr(sg, "_restore_graph_config", restore)
    monkeypatch.setattr(sg, "CodeGraphAdapter", Adapter)

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 0
    assert calls[:4] == ["capture", "init", "restore", "prepare"]
    assert snapshots[0].config_digest == hashlib.sha256(config.read_bytes()).hexdigest()


def test_setup_removes_init_created_config_before_preparing_absent_snapshot(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "codegraph.json"
    calls: list[str] = []
    original_restore = sg._restore_graph_config

    def initialize(_root):
        calls.append("init")
        config.write_bytes(b"{}")
        return True

    def restore(state):
        calls.append("restore")
        return original_restore(state)

    class Adapter:
        def prepare(self, _repo_root):
            calls.append("prepare")
            assert not config.exists()
            return GraphSnapshot(
                status="available", provider="CodeGraph", provider_version="1.1.1",
                index_version="24", repo_head="commit", config_digest="absent",
                graph_scope_digest="scope", sync_performed=True, fallback_reason=None,
            )

        def prepared_status(self, _repo_root):
            return _FRESH

    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)
    monkeypatch.setattr(sg, "_initialize_worktree_local_index", initialize)
    monkeypatch.setattr(sg, "_restore_graph_config", restore)
    monkeypatch.setattr(sg, "CodeGraphAdapter", Adapter)

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 0
    assert calls == ["init", "restore", "prepare"]


def test_prepare_rejects_snapshot_for_a_different_config_digest(monkeypatch) -> None:
    class Adapter:
        def prepare(self, _repo_root):
            return GraphSnapshot(
                status="available", provider="CodeGraph", provider_version="1.1.1",
                index_version="24", repo_head="commit", config_digest="provider-digest",
                graph_scope_digest="scope", sync_performed=True, fallback_reason=None,
            )

        def prepared_status(self, _repo_root):
            pytest.fail("mismatched snapshot status was consumed")

    monkeypatch.setattr(sg, "CodeGraphAdapter", Adapter)

    assert sg._prepare_worktree_local_index("/repo", "restored-digest") is None


def test_setup_restore_failure_aborts_before_prepare(tmp_path, monkeypatch) -> None:
    config = tmp_path / "codegraph.json"
    config.write_bytes(b"before")
    calls: list[str] = []
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)
    monkeypatch.setattr(
        sg,
        "_initialize_worktree_local_index",
        lambda _root: calls.append("init") or config.write_bytes(b"after") or True,
    )
    monkeypatch.setattr(
        sg, "_restore_graph_config", lambda _state: calls.append("restore") or False
    )
    monkeypatch.setattr(
        sg, "CodeGraphAdapter", lambda: pytest.fail("prepare ran after restore failure")
    )

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path), as_json=True)) == 1
    assert calls == ["init", "restore"]


def test_doctor_fix_restores_config_and_reports_post_fix_digest(
    tmp_path, monkeypatch, capsys
) -> None:
    raw = b'{"extensions":{}}\n'
    config = tmp_path / "codegraph.json"
    config.write_bytes(raw)
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(sg, "_status", lambda _root: None)

    def mutate(_root):
        config.unlink()
        return True

    monkeypatch.setattr(sg, "_initialize_worktree_local_index", mutate)
    monkeypatch.setattr(sg, "_prepare_worktree_local_index", lambda *_args: _FRESH)

    assert sg.run_doctor(
        _args(repo_root=str(tmp_path), as_json=True, fix_graph=True)
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    digest = hashlib.sha256(raw).hexdigest()
    assert config.read_bytes() == raw
    assert payload["post_fix_config_digest"] == digest
    assert payload["graph_config"]["digest"] == digest


def test_doctor_fix_restores_config_before_prepare(tmp_path, monkeypatch, capsys) -> None:
    raw = b'{"extensions":{}}\n'
    config = tmp_path / "codegraph.json"
    config.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    calls: list[str] = []
    original_restore = sg._restore_graph_config

    def initialize(_root):
        calls.append("init")
        config.unlink()
        return True

    def restore(state):
        calls.append("restore")
        return original_restore(state)

    class Adapter:
        def prepare(self, _repo_root):
            calls.append("prepare")
            assert config.read_bytes() == raw
            return GraphSnapshot(
                status="available", provider="CodeGraph", provider_version="1.1.1",
                index_version="24", repo_head="commit", config_digest=digest,
                graph_scope_digest="scope", sync_performed=True, fallback_reason=None,
            )

        def prepared_status(self, _repo_root):
            return _FRESH

    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(sg, "_status", lambda _root: None)
    monkeypatch.setattr(sg, "_initialize_worktree_local_index", initialize)
    monkeypatch.setattr(sg, "_restore_graph_config", restore)
    monkeypatch.setattr(sg, "CodeGraphAdapter", Adapter)

    assert sg.run_doctor(
        _args(repo_root=str(tmp_path), as_json=True, fix_graph=True)
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == ["init", "restore", "prepare"]
    assert payload["post_fix_config_digest"] == digest


def test_doctor_restore_failure_aborts_before_prepare(tmp_path, monkeypatch) -> None:
    config = tmp_path / "codegraph.json"
    config.write_bytes(b"before")
    calls: list[str] = []
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(sg, "_status", lambda _root: None)
    monkeypatch.setattr(
        sg,
        "_initialize_worktree_local_index",
        lambda _root: calls.append("init") or config.write_bytes(b"after") or True,
    )
    monkeypatch.setattr(
        sg, "_restore_graph_config", lambda _state: calls.append("restore") or False
    )
    monkeypatch.setattr(
        sg, "CodeGraphAdapter", lambda: pytest.fail("prepare ran after restore failure")
    )

    assert sg.run_doctor(
        _args(repo_root=str(tmp_path), as_json=True, fix_graph=True)
    ) == 1
    assert calls == ["init", "restore"]


def test_doctor_json_includes_graph_configuration(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "codegraph.json").write_text(
        json.dumps({"includeIgnored": ["vendor/**"]}), encoding="utf-8"
    )
    monkeypatch.setattr(sg, "_installed", lambda: True)
    monkeypatch.setattr(sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(sg, "_run", _Engine([_FRESH]))

    assert sg.run_doctor(_args(repo_root=str(tmp_path), as_json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["graph_config"]["exists"] is True
    assert payload["graph_config"]["include_ignored"] == ["vendor/**"]
    assert payload["graph_config"]["supported_fields"] == ["extensions", "includeIgnored"]


def test_setup_preserves_existing_config_bytes_and_prepares_after_init(
    tmp_path, monkeypatch
) -> None:
    raw = b'{"extensions":{".extx":"python"}}\n'
    config = tmp_path / "codegraph.json"
    config.write_bytes(raw)
    monkeypatch.setattr(sg, "_ensure_installed", lambda *a, **k: None)
    engine = _Engine([])
    monkeypatch.setattr(sg, "_run", engine)
    calls: list[str] = []

    class Adapter:
        def prepare(self, repo_root):
            calls.append(repo_root)
            return GraphSnapshot(
                status="available", provider="CodeGraph", provider_version="1.1.1",
                index_version="24", repo_head="commit",
                config_digest=hashlib.sha256(raw).hexdigest(),
                graph_scope_digest="scope", sync_performed=True, fallback_reason=None,
            )

        def prepared_status(self, _repo_root):
            return _FRESH

    monkeypatch.setattr(sg, "CodeGraphAdapter", Adapter)

    assert sg.run_setup_graph(_args(repo_root=str(tmp_path))) == 0
    assert config.read_bytes() == raw
    assert calls == [str(tmp_path)]
    assert any(call[1:] == ["init", str(tmp_path)] for call in engine.calls)
