"""TDD for node_harness — the JS/TS build+test OUTCOME primitive (sibling of dotnet_harness).

Fully deterministic: the pure helpers (package-manager detection, the FIXED build/test argv profiles,
TS/bundler error scanning, Vitest JSON parsing) are tested directly, and run_build/run_tests are driven
through an injected fake runner so no real node/npm/pnpm is ever invoked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from e2e.external.utils import node_harness as nh


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _fake_runner(script):
    """A runner that returns queued CompletedProcess-likes in order (one per subprocess call)."""
    calls = []

    def run(argv, *, cwd, timeout, env):
        calls.append(argv)
        return script[len(calls) - 1]

    run.calls = calls
    return run


def _pin_node(root):
    (root / "package.json").write_text('{"engines":{"node":">=20"}}', encoding="utf-8")


# ---- package-manager detection (fixed, lockfile-driven, fail-closed) ----

def test_detect_pnpm_yarn_npm(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert nh.detect_package_manager(tmp_path) == "pnpm"


def test_detect_none_without_lockfile(tmp_path):
    assert nh.detect_package_manager(tmp_path) is None


def test_detect_none_with_ambiguous_lockfiles(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("", encoding="utf-8")
    assert nh.detect_package_manager(tmp_path) is None


# ---- fixed profile argv (NEVER caller-supplied; corpus JSON can't inject shell) ----

def test_build_argv_is_the_fixed_pm_build_script():
    assert nh._build_argv("pnpm") == ["pnpm", "run", "build"]
    assert nh._build_argv("npm") == ["npm", "run", "build"]


def test_build_argv_zshy_profile_is_a_filtered_typecheck():
    assert nh._build_argv("pnpm", profile="zshy", selector="zod:tsconfig.build.json") == [
        "pnpm", "--filter", "zod", "exec", "zshy", "--project", "tsconfig.build.json"
    ]


def test_build_argv_rejects_unknown_profile():
    try:
        nh._build_argv("pnpm", profile="typo")
    except ValueError as exc:
        assert "profile" in str(exc)
    else:
        raise AssertionError("unknown build_profile must fail closed")


def test_run_build_zshy_requires_a_selector(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    r = nh.run_build(tmp_path, profile="zshy", selector=None, runner=_fake_runner([]))
    assert r.available is False and "selector" in r.error_summary


def test_run_build_zshy_rejects_empty_selector_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    for selector in (":tsconfig.build.json", "zod:"):
        r = nh.run_build(tmp_path, profile="zshy", selector=selector, runner=_fake_runner([]))
        assert r.available is False and "selector" in r.error_summary


def test_run_build_zshy_requires_pnpm_even_with_other_lockfiles(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    r = nh.run_build(tmp_path, profile="zshy", selector="zod:tsconfig.build.json",
                     runner=_fake_runner([]))
    assert r.available is False and "pnpm" in r.error_summary


def test_run_build_rejects_unknown_profile_before_build(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    _pin_node(tmp_path)
    (tmp_path / "node_modules").mkdir()
    r = nh.run_build(tmp_path, profile="typo", runner=_fake_runner([]))
    assert r.available is False and r.ran is False
    assert "profile" in r.error_summary


def test_run_build_zshy_dispatches_the_typecheck(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"engines":{"node":">=20"}}', encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    runner = _fake_runner([_proc(0)])
    r = nh.run_build(tmp_path, profile="zshy", selector="zod:tsconfig.build.json", runner=runner)
    assert r.passed is True
    assert runner.calls == [["pnpm", "--filter", "zod", "exec", "zshy", "--project", "tsconfig.build.json"]]


def test_run_build_zshy_fails_if_build_mutates_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    monkeypatch.setattr(nh.shutil, "which", lambda name: f"/bin/{name}")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    _pin_node(tmp_path)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src.ts").write_text("before\n", encoding="utf-8")
    nh.subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True)
    nh.subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, text=True)
    nh.subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "base"],
        cwd=tmp_path, capture_output=True, text=True,
    )

    def _mutating_runner(argv, *, cwd, timeout, env):
        (tmp_path / "src.ts").write_text("after\n", encoding="utf-8")
        return _proc(0)

    r = nh.run_build(tmp_path, profile="zshy", selector="zod:tsconfig.build.json",
                     runner=_mutating_runner)

    assert r.passed is False
    assert "mutated" in r.error_summary


def test_test_argv_is_vitest_json_with_optional_file_and_filter():
    argv = nh._test_argv("pnpm", test_path="src/x.test.ts", test_filter="handles empty")
    assert argv[:4] == ["pnpm", "exec", "vitest", "run"]
    assert "src/x.test.ts" in argv and "--reporter=json" in argv
    assert argv[-2:] == ["-t", "handles empty"]


# ---- pure parsing ----

def test_scan_build_errors_picks_ts_and_bundler_lines():
    out = "ok\nsrc/a.ts(3,5): error TS2345: bad\nnoise\n✘ [ERROR] boom\n"
    errs = nh._scan_build_errors(out)
    assert any("TS2345" in e for e in errs)
    assert any("boom" in e for e in errs)


def test_parse_vitest_json_reads_totals():
    payload = json.dumps({"numTotalTests": 12, "numFailedTests": 2})
    assert nh._parse_vitest(payload) == (12, 2)


def test_parse_vitest_json_tolerates_leading_noise():
    payload = "install noise\n" + json.dumps({"numTotalTests": 3, "numFailedTests": 0}) + "\n"
    assert nh._parse_vitest(payload) == (3, 0)


# ---- run_build (via fake runner) ----

def test_run_build_fails_closed_without_lockfile(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    r = nh.run_build(tmp_path, runner=_fake_runner([]))
    assert r.available is False and r.ran is False
    assert "lockfile" in r.error_summary.lower()


def test_run_build_fails_closed_on_ambiguous_lockfiles(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    r = nh.run_build(tmp_path, runner=_fake_runner([]))
    assert r.available is False and r.ran is False
    assert "ambiguous" in r.error_summary.lower()


def test_run_build_missing_package_manager_returns_failed_result(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    monkeypatch.setattr(nh.shutil, "which", lambda name: None if name == "pnpm" else f"/bin/{name}")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    _pin_node(tmp_path)
    (tmp_path / "node_modules").mkdir()
    r = nh.run_build(tmp_path)
    assert r.available is True and r.ran is True
    assert r.passed is False and r.exit_code == 127
    assert "executable not found" in r.error_summary.lower()


def test_run_build_installs_then_builds_and_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")  # pm = pnpm, node_modules absent
    _pin_node(tmp_path)
    runner = _fake_runner([_proc(0), _proc(0)])  # install ok, build ok
    r = nh.run_build(tmp_path, runner=runner)
    assert r.ran is True and r.passed is True and r.exit_code == 0
    assert runner.calls[0][:2] == ["pnpm", "install"]      # installed first (node_modules missing)
    assert runner.calls[1] == ["pnpm", "run", "build"]     # then the fixed build script


def test_run_build_surfaces_ts_errors_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    _pin_node(tmp_path)
    (tmp_path / "node_modules").mkdir()  # deps present -> skip install
    runner = _fake_runner([_proc(2, stdout="src/z.ts(1,1): error TS2739: missing members")])
    r = nh.run_build(tmp_path, runner=runner)
    assert r.passed is False and r.exit_code == 2
    assert "TS2739" in r.error_summary
    assert runner.calls == [["pnpm", "run", "build"]]  # no install call (node_modules present)


def test_run_tests_targets_file_and_reports_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    _pin_node(tmp_path)
    (tmp_path / "node_modules").mkdir()
    runner = _fake_runner([_proc(0, stdout=json.dumps({"numTotalTests": 5, "numFailedTests": 0}))])
    r = nh.run_tests(tmp_path, test_path="src/a.test.ts", runner=runner)
    assert r.ran is True and r.passed is True and r.tests_selected == 5


def test_run_tests_zero_selected_targeted_is_not_a_pass(tmp_path, monkeypatch):
    # A targeted test run that selected 0 tests must NOT count as passing (fabricated-pass trap).
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    _pin_node(tmp_path)
    (tmp_path / "node_modules").mkdir()
    runner = _fake_runner([_proc(0, stdout=json.dumps({"numTotalTests": 0, "numFailedTests": 0}))])
    r = nh.run_tests(tmp_path, test_path="src/a.test.ts", runner=runner)
    assert r.passed is False


def test_run_tests_targeted_malformed_json_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    _pin_node(tmp_path)
    (tmp_path / "node_modules").mkdir()
    runner = _fake_runner([_proc(0, stdout="not json")])
    r = nh.run_tests(tmp_path, test_path="src/a.test.ts", runner=runner)
    assert r.passed is False and r.tests_selected is None


def test_run_build_fails_closed_without_node_version_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "node_available", lambda: True)
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    r = nh.run_build(tmp_path, runner=_fake_runner([]))
    assert r.available is False and r.ran is False
    assert "node version" in r.error_summary.lower()
