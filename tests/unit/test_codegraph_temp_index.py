from __future__ import annotations

import importlib
import json
import os

import pytest

from pebra.adapters.bounded_process import BoundedProcessResult


def _subject():
    try:
        subject = importlib.import_module("pebra.adapters.codegraph_temp_index")
        if hasattr(subject, "_KNOWN_ENGINE_VERSIONS"):
            subject._KNOWN_ENGINE_VERSIONS.clear()
        return subject
    except ModuleNotFoundError:
        pytest.fail("shared temporary CodeGraph index boundary is missing")


def _result(stdout: str = "", *, returncode: int = 0) -> BoundedProcessResult:
    return BoundedProcessResult(returncode, stdout, "", False, False, None)


def test_temp_index_pins_cwd_environment_and_checks_status(tmp_path, monkeypatch) -> None:
    subject = _subject()
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setenv("CODEGRAPH_DIR", ".codegraph-host")
    monkeypatch.setattr(subject, "find_engine", lambda: "/tools/codegraph")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-1] == "--version":
            return _result("1.1.9\n")
        if "init" in argv:
            database = root / ".codegraph" / "codegraph.db"
            database.parent.mkdir()
            database.write_bytes(b"db")
            return _result()
        return _result(json.dumps({
            "version": "1.1.9",
            "indexPath": str(root / ".codegraph"),
        }))

    monkeypatch.setattr(subject, "run_bounded", run)

    assert subject.index_temp_tree(root, timeout_s=7) == root / ".codegraph" / "codegraph.db"
    assert [call[0][1:] for call in calls] == [
        ["--version"],
        ["init", str(root)],
        ["status", str(root), "--json"],
    ]
    for _argv, kwargs in calls:
        assert kwargs["cwd"] == str(root)
        assert kwargs["env"]["CODEGRAPH_DIR"] == ".codegraph"
        assert kwargs["env"] is not os.environ
        assert kwargs["timeout"] <= 7


def test_temp_index_rejects_out_of_range_engine_before_init(tmp_path, monkeypatch) -> None:
    subject = _subject()
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(subject, "find_engine", lambda: "/tools/codegraph")
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _result("1.2.0\n")

    monkeypatch.setattr(subject, "run_bounded", run)

    with pytest.raises(subject.GraphEngineVersionRejected):
        subject.index_temp_tree(root)
    assert [call[1:] for call in calls] == [["--version"]]


def test_temp_index_rejects_status_version_different_from_preflight(
    tmp_path, monkeypatch
) -> None:
    subject = _subject()
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(subject, "find_engine", lambda: "/tools/codegraph")

    def run(argv, **_kwargs):
        if argv[-1] == "--version":
            return _result("1.1.9\n")
        if "init" in argv:
            database = root / ".codegraph" / "codegraph.db"
            database.parent.mkdir()
            database.write_bytes(b"db")
            return _result()
        return _result(json.dumps({
            "version": "1.2.0",
            "indexPath": str(root / ".codegraph"),
        }))

    monkeypatch.setattr(subject, "run_bounded", run)

    with pytest.raises(subject.GraphEngineVersionRejected):
        subject.index_temp_tree(root)


def test_temp_index_rejects_status_pointing_outside_isolated_cache(
    tmp_path, monkeypatch
) -> None:
    subject = _subject()
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(subject, "find_engine", lambda: "/tools/codegraph")

    def run(argv, **_kwargs):
        if argv[-1] == "--version":
            return _result("1.1.9\n")
        if "init" in argv:
            return _result()
        return _result(json.dumps({
            "version": "1.1.9",
            "indexPath": str(tmp_path / "host-index"),
        }))

    monkeypatch.setattr(subject, "run_bounded", run)

    with pytest.raises(subject.TempIndexIsolationError):
        subject.index_temp_tree(root)


def test_known_engine_version_is_stable_for_process_lifetime(tmp_path, monkeypatch) -> None:
    subject = _subject()
    versions = iter(("1.1.1", "1.1.9"))
    monkeypatch.setattr(subject, "read_engine_version", lambda *_args, **_kwargs: next(versions))

    assert subject.known_engine_version("/tools/codegraph", root=tmp_path) == "1.1.1"
    assert subject.known_engine_version("/tools/codegraph", root=tmp_path) == "1.1.1"
