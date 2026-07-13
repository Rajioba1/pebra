"""Phase E1 (unit): the pure parsing/exit-check helpers of the CLI harness. No subprocess, no pebra."""

from __future__ import annotations

import subprocess

import pytest

from e2e.utils import cli_harness as ch


def test_parse_json_stdout_ok():
    assert ch._parse_json_stdout('{"a": 1}', ["pebra", "assess"]) == {"a": 1}


def test_parse_json_stdout_raises_with_raw_stdout_on_bad_json():
    with pytest.raises(ch.CLIError) as exc:
        ch._parse_json_stdout("not json at all", ["pebra", "assess"])
    assert "not json at all" in str(exc.value)  # the raw stdout is surfaced for debugging


def test_check_exit_ok_is_silent():
    ch._check_exit(0, ["pebra", "assess"], "")  # must not raise


def test_check_exit_raises_with_stderr_on_nonzero():
    with pytest.raises(ch.CLIError) as exc:
        ch._check_exit(2, ["pebra", "assess"], "boom: bad request")
    assert "boom: bad request" in str(exc.value)
    assert "2" in str(exc.value)  # the exit code is surfaced


def test_run_uses_a_timeout(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)

        class Proc:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return Proc()

    monkeypatch.setattr(ch.subprocess, "run", fake_run)

    ch._run(["assess", "request.json", "--json"])

    assert captured["timeout"] == ch.DEFAULT_TIMEOUT_SECONDS


def test_assess_forwards_extra_env(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run_json(args, *, extra_env=None, timeout=None):
        captured["args"] = args
        captured["extra_env"] = extra_env
        captured["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr(ch, "_run_json", fake_run_json)

    assert ch.assess(
        tmp_path / "request.json",
        repo_root=tmp_path,
        db=tmp_path / "p.db",
        extra_env={"CODEGRAPH_DIR": str(tmp_path / "no-index")},
    ) == {"ok": True}

    assert captured["extra_env"] == {"CODEGRAPH_DIR": str(tmp_path / "no-index")}
    assert captured["timeout"] == ch.DEFAULT_TIMEOUT_SECONDS


def test_source_neutral_graph_setup_restores_gitignore_and_ignores_runtime_dirs(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("dist/\n", encoding="utf-8")
    (tmp_path / "src.ts").write_text("export const value = 1;\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)

    def fake_setup(repo_root):
        with (repo_root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write(".codegraph/\n")
        (repo_root / ".codegraph").mkdir()
        (repo_root / ".codegraph" / "codegraph.db").write_bytes(b"graph")
        (repo_root / ".pebra").mkdir()

    ch.run_source_neutral_graph_setup(tmp_path, fake_setup)

    assert gitignore.read_text(encoding="utf-8") == "dist/\n"
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout == ""
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".pebra/" in exclude
    assert ".codegraph/" in exclude
