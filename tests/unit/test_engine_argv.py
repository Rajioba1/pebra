"""Safe direct CodeGraph launcher resolution; Windows command shims are never executed."""

from __future__ import annotations

import pytest

from pebra.core import engine_argv as ea


def test_posix_bare_name_resolved_not_wrapped(monkeypatch) -> None:
    monkeypatch.setattr(ea.shutil, "which", lambda n: "/usr/local/bin/codegraph")
    assert ea._resolve_engine_argv("codegraph", ["status", "/repo", "--json"], os_name="posix") == \
        ["/usr/local/bin/codegraph", "status", "/repo", "--json"]


def test_windows_managed_cmd_resolves_direct_node_layout(tmp_path, monkeypatch) -> None:
    root = tmp_path / "managed"
    launcher = root / "bin" / "codegraph.cmd"
    node = root / "node.exe"
    script = root / "lib" / "dist" / "bin" / "codegraph.js"
    launcher.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    launcher.write_text("shim", encoding="utf-8")
    node.write_bytes(b"node")
    script.write_text("script", encoding="utf-8")
    monkeypatch.setattr(ea.shutil, "which", lambda name: str(launcher) if name == "codegraph" else None)

    assert ea._resolve_engine_argv("codegraph", ["status", "/repo"], os_name="nt") == \
        [str(node), "--liftoff-only", str(script), "status", "/repo"]


def test_windows_npm_codegraph_shim_resolves_sibling_node_and_script(tmp_path, monkeypatch) -> None:
    launcher = tmp_path / "npm" / "codegraph.cmd"
    node = launcher.parent / "node.exe"
    script = launcher.parent / "node_modules" / "@colbymchenry" / "codegraph" / "dist" / "bin" / "codegraph.js"
    script.parent.mkdir(parents=True)
    launcher.write_text("shim", encoding="utf-8")
    node.write_bytes(b"node")
    script.write_text("script", encoding="utf-8")

    assert ea._resolve_engine_argv(str(launcher), ["explore", "a&b"], os_name="nt") == [
        str(node), str(script), "explore", "a&b",
    ]


def test_windows_npm_codegraph_shim_can_use_resolved_node(tmp_path, monkeypatch) -> None:
    launcher = tmp_path / "npm" / "codegraph.cmd"
    script = launcher.parent / "node_modules" / "@colbymchenry" / "codegraph" / "dist" / "bin" / "codegraph.js"
    resolved_node = tmp_path / "nodejs" / "node.exe"
    script.parent.mkdir(parents=True)
    resolved_node.parent.mkdir()
    launcher.write_text("shim", encoding="utf-8")
    script.write_text("script", encoding="utf-8")
    resolved_node.write_bytes(b"node")
    monkeypatch.setattr(ea.shutil, "which", lambda name: str(resolved_node) if name == "node" else None)

    assert ea._resolve_engine_argv(str(launcher), ["status"], os_name="nt") == [
        str(resolved_node), str(script), "status",
    ]


def test_windows_npm_shim_resolves_direct_node_and_npm_cli(tmp_path, monkeypatch) -> None:
    launcher = tmp_path / "nodejs" / "npm.cmd"
    node = launcher.parent / "node.exe"
    script = launcher.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    script.parent.mkdir(parents=True)
    launcher.write_text("shim", encoding="utf-8")
    node.write_bytes(b"node")
    script.write_text("script", encoding="utf-8")

    assert ea._resolve_engine_argv(
        str(launcher), ["install", "a&|<>^()%!"], os_name="nt"
    ) == [
        str(node), str(script), "install", "a&|<>^()%!",
    ]


def test_windows_npm_shim_can_use_path_node_for_prefix_layout(tmp_path, monkeypatch) -> None:
    launcher = tmp_path / "prefix" / "npm.cmd"
    script = launcher.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    node = tmp_path / "nodejs" / "node.exe"
    script.parent.mkdir(parents=True)
    node.parent.mkdir()
    launcher.write_text("shim", encoding="utf-8")
    script.write_text("script", encoding="utf-8")
    node.write_bytes(b"node")
    monkeypatch.setattr(ea.shutil, "which", lambda name: str(node) if name == "node" else None)

    assert ea._resolve_engine_argv(str(launcher), ["--version"], os_name="nt") == [
        str(node), str(script), "--version",
    ]


def test_windows_npm_layout_rejects_resolved_node_command_shim(tmp_path, monkeypatch) -> None:
    launcher = tmp_path / "npm" / "codegraph.cmd"
    script = launcher.parent / "node_modules" / "@colbymchenry" / "codegraph" / "dist" / "bin" / "codegraph.js"
    unsafe_node = tmp_path / "node.cmd"
    script.parent.mkdir(parents=True)
    launcher.write_text("shim", encoding="utf-8")
    script.write_text("script", encoding="utf-8")
    unsafe_node.write_text("shim", encoding="utf-8")
    monkeypatch.setattr(ea.shutil, "which", lambda name: str(unsafe_node) if name == "node" else None)

    with pytest.raises(ea.UnsafeEngineLauncherError):
        ea._resolve_engine_argv(str(launcher), ["status"], os_name="nt")


def test_bare_name_not_on_path_returns_unresolved(monkeypatch) -> None:
    # not found -> return bare so subprocess raises FileNotFoundError (preserves 'engine absent')
    monkeypatch.setattr(ea.shutil, "which", lambda n: None)
    assert ea.resolve_engine_argv("codegraph", ["status"]) == ["codegraph", "status"]


@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_unknown_windows_command_shim_fails_with_stable_guidance(tmp_path, monkeypatch, suffix) -> None:
    launcher = tmp_path / f"arbitrary{suffix}"
    launcher.write_text("echo unsafe", encoding="utf-8")

    with pytest.raises(ea.UnsafeEngineLauncherError) as exc:
        ea._resolve_engine_argv(str(launcher), ["query&whoami"], os_name="nt")

    assert str(exc.value) == "unsupported CodeGraph launcher; run: pebra setup-graph --fix"
    assert str(launcher) not in str(exc.value)


def test_windows_metacharacters_remain_literal_argv_for_safe_layout(tmp_path, monkeypatch) -> None:
    root = tmp_path / "managed & root"
    launcher = root / "bin" / "codegraph.cmd"
    node = root / "node.exe"
    script = root / "lib" / "dist" / "bin" / "codegraph.js"
    launcher.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    launcher.write_text("shim", encoding="utf-8")
    node.write_bytes(b"node")
    script.write_text("script", encoding="utf-8")
    values = ["q&|<>^()%!", "file&|<>^()%!.py", r"C:\repo &|<>^()%!"]

    argv = ea._resolve_engine_argv(str(launcher), values, os_name="nt")

    assert argv == [str(node), "--liftoff-only", str(script), *values]
    assert "cmd" not in [part.lower() for part in argv]


def test_full_posix_path_passed_through(monkeypatch) -> None:
    monkeypatch.setattr(ea.shutil, "which", lambda n: (_ for _ in ()).throw(AssertionError("no which")))
    assert ea._resolve_engine_argv(
        "/opt/cg/bin/codegraph", ["sync", "/r"], os_name="posix"
    ) == \
        ["/opt/cg/bin/codegraph", "sync", "/r"]
