"""M5c.5 / A2 — resolve_engine_argv: cross-platform invocation of the graph engine CLI.

The Windows hazard: codegraph/npm install as `.cmd` shims (no `.exe`); a bare name fails to spawn and a
full-path `.cmd` under shell=False is fragile on CPython 3.12.4+. The resolver wraps `.cmd`/`.bat` in
`cmd /c`. Pure — mocks shutil.which + os.name, no subprocess."""

from __future__ import annotations

from pebra.core import engine_argv as ea


def test_posix_bare_name_resolved_not_wrapped(monkeypatch) -> None:
    monkeypatch.setattr(ea.os, "name", "posix")
    monkeypatch.setattr(ea.shutil, "which", lambda n: "/usr/local/bin/codegraph")
    assert ea.resolve_engine_argv("codegraph", ["status", "/repo", "--json"]) == \
        ["/usr/local/bin/codegraph", "status", "/repo", "--json"]


def test_windows_cmd_suffix_wraps_with_cmd_c(monkeypatch) -> None:
    monkeypatch.setattr(ea.os, "name", "nt")
    monkeypatch.setattr(ea.shutil, "which", lambda n: r"C:\tools\bin\codegraph.CMD")
    assert ea.resolve_engine_argv("codegraph", ["status", "/repo"]) == \
        ["cmd", "/c", r"C:\tools\bin\codegraph.CMD", "status", "/repo"]


def test_windows_bat_suffix_also_wraps(monkeypatch) -> None:
    monkeypatch.setattr(ea.os, "name", "nt")
    monkeypatch.setattr(ea.shutil, "which", lambda n: r"C:\tools\npm.bat")
    assert ea.resolve_engine_argv("npm", ["install"]) == ["cmd", "/c", r"C:\tools\npm.bat", "install"]


def test_bare_name_not_on_path_returns_unresolved(monkeypatch) -> None:
    # not found -> return bare so subprocess raises FileNotFoundError (preserves 'engine absent')
    monkeypatch.setattr(ea.shutil, "which", lambda n: None)
    assert ea.resolve_engine_argv("codegraph", ["status"]) == ["codegraph", "status"]


def test_full_windows_cmd_path_skips_which_and_wraps(monkeypatch) -> None:
    monkeypatch.setattr(ea.os, "name", "nt")

    def _boom(_n):
        raise AssertionError("which must not be called for a full path")

    monkeypatch.setattr(ea.shutil, "which", _boom)
    assert ea.resolve_engine_argv(r"C:\tools\bin\codegraph.cmd", ["--version"]) == \
        ["cmd", "/c", r"C:\tools\bin\codegraph.cmd", "--version"]


def test_full_posix_path_passed_through(monkeypatch) -> None:
    monkeypatch.setattr(ea.os, "name", "posix")
    monkeypatch.setattr(ea.shutil, "which", lambda n: (_ for _ in ()).throw(AssertionError("no which")))
    assert ea.resolve_engine_argv("/opt/cg/bin/codegraph", ["sync", "/r"]) == \
        ["/opt/cg/bin/codegraph", "sync", "/r"]
