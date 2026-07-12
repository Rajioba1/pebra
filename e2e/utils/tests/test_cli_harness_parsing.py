"""Phase E1 (unit): the pure parsing/exit-check helpers of the CLI harness. No subprocess, no pebra."""

from __future__ import annotations

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
