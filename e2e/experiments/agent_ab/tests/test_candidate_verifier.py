"""Candidate verifier: run a revised candidate's covering tests and emit the evidence PEBRA's
gate 7 consumes (status + required_checks + verified_patch_hash). No `import pebra` (the hash is the
documented wire convention, recomputed here); the backend is monkeypatched so these stay pure."""

from __future__ import annotations

import hashlib
import subprocess
from types import SimpleNamespace

from e2e.experiments.agent_ab.tools import candidate_verifier as cv

_PATCH = (
    "diff --git a/src/Numerics/SpecialFunctions/Gamma.cs b/src/Numerics/SpecialFunctions/Gamma.cs\n"
    "--- a/src/Numerics/SpecialFunctions/Gamma.cs\n"
    "+++ b/src/Numerics/SpecialFunctions/Gamma.cs\n"
    "@@ -1,2 +1,2 @@\n-old\n+narrowed\n"
)


def _stub_tests(monkeypatch, *, available=True, ran=True, passed=True, selected=7):
    class FakeBackend:
        def run_build(self, repo_root, spec):
            raise AssertionError("run_build should not be called")

        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            return SimpleNamespace(
                available=available, ran=ran, passed=passed, exit_code=0 if passed else 1,
                error_summary="" if passed else "GammaTests.SomeCase FAILED", duration_seconds=0.1,
                tests_selected=selected,
            )

    monkeypatch.setattr(cv.backends, "get_backend", lambda language: FakeBackend())


def test_hash_matches_wire_convention():
    # Must equal decision_engine.candidate_patch_hash: sha256 hexdigest of the exact UTF-8 patch text.
    assert cv.candidate_patch_hash(_PATCH) == hashlib.sha256(_PATCH.encode("utf-8")).hexdigest()


def test_passing_covering_tests_yield_bound_passed_evidence(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    ev = cv.verify_candidate(
        repo_path=tmp_path, patch_text=_PATCH, language="csharp",
        test_project="tests/Numerics.Tests/Numerics.Tests.csproj", test_filter="FullyQualifiedName~Gamma")
    assert ev["status"] == "passed"
    assert ev["required_checks"] == ["covering_tests"]
    assert ev["checks"]["covering_tests"] == "passed"
    assert ev["verified_patch_hash"] == hashlib.sha256(_PATCH.encode("utf-8")).hexdigest()
    assert ev["domain"] == "covering_tests"


def test_zero_selected_tests_cannot_yield_passed_evidence(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True, selected=0)

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
    )

    assert ev["status"] == "unavailable"
    assert ev["provenance"]["tests_selected"] == 0


def test_failing_covering_tests_yield_failed_evidence_still_bound(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=False)
    ev = cv.verify_candidate(repo_path=tmp_path, patch_text=_PATCH, language="csharp",
                             test_project="tests/Numerics.Tests/Numerics.Tests.csproj")
    assert ev["status"] == "failed"
    assert ev["checks"]["covering_tests"] == "failed"
    # hash still binds the patch we ran, so a failed proof cannot be swapped for a passed one either
    assert ev["verified_patch_hash"] == hashlib.sha256(_PATCH.encode("utf-8")).hexdigest()


def test_dotnet_absent_is_unavailable_not_failed(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, available=False, ran=False, passed=False)
    ev = cv.verify_candidate(repo_path=tmp_path, patch_text=_PATCH, language="csharp",
                             test_project="tests/Numerics.Tests/Numerics.Tests.csproj")
    assert ev["status"] == "unavailable"  # honest absence: PEBRA must keep the write blocked, not proceed


def test_available_but_did_not_run_is_unavailable(tmp_path, monkeypatch):
    # exercises the OTHER branch of `not available or not ran`: SDK present but the run didn't execute
    _stub_tests(monkeypatch, available=True, ran=False, passed=True)
    ev = cv.verify_candidate(repo_path=tmp_path, patch_text=_PATCH, language="csharp",
                             test_project="tests/Numerics.Tests/Numerics.Tests.csproj")
    assert ev["status"] == "unavailable"  # a claimed pass on a run that never happened must not proceed


def test_no_covering_tests_declared_is_unavailable(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    ev = cv.verify_candidate(repo_path=tmp_path, patch_text=_PATCH, language="csharp", test_project=None)
    # can't certify a candidate with no covering tests to run -> unavailable, never a fabricated pass
    assert ev["status"] == "unavailable"


def test_explicit_build_fallback_can_verify_javascript_candidate(tmp_path, monkeypatch):
    seen = {}

    class FakeBackend:
        def run_build(self, repo_root, spec):
            seen.update({
                "language": spec.language,
                "harness_id": spec.harness_id,
                "build_profile": spec.build_profile,
                "build_selector": spec.build_selector,
            })
            return SimpleNamespace(
                available=True, ran=True, passed=True, exit_code=0, error_summary="",
                duration_seconds=0.1,
            )

        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            raise AssertionError("run_tests should not be called for build fallback")

    monkeypatch.setattr(cv.backends, "get_backend", lambda language: FakeBackend())
    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_PATCH,
        language="typescript",
        test_project=None,
        harness_id="node",
        build_profile="zshy",
        build_selector="zod:tsconfig.build.json",
        allow_build_fallback=True,
    )

    assert ev["status"] == "passed"
    assert ev["required_checks"] == ["candidate_build"]
    assert ev["checks"] == {"candidate_build": "passed"}
    assert ev["domain"] == "candidate_build"
    assert seen == {
        "language": "typescript",
        "harness_id": "node",
        "build_profile": "zshy",
        "build_selector": "zod:tsconfig.build.json",
    }


def test_required_build_runs_before_covering_tests(tmp_path, monkeypatch):
    calls = []

    class FakeBackend:
        def run_build(self, repo_root, spec):
            calls.append("build")
            return SimpleNamespace(
                available=True, ran=True, passed=True, exit_code=0, error_summary="",
                duration_seconds=0.1,
            )

        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            calls.append("tests")
            return SimpleNamespace(
                available=True, ran=True, passed=True, exit_code=0, error_summary="",
                duration_seconds=0.1, tests_selected=4,
            )

    monkeypatch.setattr(cv.backends, "get_backend", lambda _language: FakeBackend())

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
        required_checks=("candidate_build",),
    )

    assert calls == ["build", "tests"]
    assert ev["status"] == "passed"
    assert ev["checks"] == {"candidate_build": "passed", "covering_tests": "passed"}
    assert ev["required_checks"] == ["candidate_build", "covering_tests"]


def test_required_build_failure_stops_before_covering_tests(tmp_path, monkeypatch):
    class FakeBackend:
        def run_build(self, repo_root, spec):
            return SimpleNamespace(
                available=True, ran=True, passed=False, exit_code=1,
                error_summary="typecheck failed", duration_seconds=0.1,
            )

        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            raise AssertionError("tests must not run after a failed required build")

    monkeypatch.setattr(cv.backends, "get_backend", lambda _language: FakeBackend())

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
        required_checks=("candidate_build",),
    )

    assert ev["status"] == "failed"
    assert ev["checks"] == {"candidate_build": "failed"}
    assert ev["required_checks"] == ["candidate_build"]
    assert ev["reason"] == "typecheck failed"


def test_unknown_required_check_fails_closed(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_PATCH,
        language="csharp",
        test_project="tests/A.csproj",
        required_checks=("semantic_equivalence",),
    )

    assert ev["status"] == "unavailable"
    assert "semantic_equivalence" in ev["reason"]


def test_required_public_contract_cannot_pass_when_not_applicable(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
        required_checks=("public_contract_preserved",),
    )

    assert ev["status"] == "unavailable"
    assert ev["required_checks"] == ["public_contract_preserved"]


def test_build_and_tests_share_one_timeout_budget(tmp_path, monkeypatch):
    seen = []

    class FakeBackend:
        def run_build(self, repo_root, spec):
            seen.append(("build", spec.command_timeout))
            return SimpleNamespace(
                available=True, ran=True, passed=True, exit_code=0, error_summary="",
                duration_seconds=0.1,
            )

        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            seen.append(("tests", spec.command_timeout))
            return SimpleNamespace(
                available=True, ran=True, passed=True, exit_code=0, error_summary="",
                duration_seconds=0.1, tests_selected=2,
            )

    monkeypatch.setattr(cv.backends, "get_backend", lambda _language: FakeBackend())
    ticks = iter((100.0, 100.0, 104.0))
    monkeypatch.setattr(cv.time, "monotonic", lambda: next(ticks))

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_PATCH,
        language="csharp",
        test_project="tests/A.csproj",
        required_checks=("candidate_build",),
        timeout=10,
    )

    assert ev["status"] == "passed"
    assert seen == [("build", 10), ("tests", 6)]


def test_completed_checks_for_verify_maps_covering_tests_to_production_guidance():
    assert cv.completed_checks_for_verify({
        "checks": {
            "candidate_build": "passed",
            "covering_tests": "passed",
            "public_contract_preserved": "passed",
        }
    }) == {
        "candidate_build": "passed",
        "run targeted tests for the touched scope before commit": "passed",
        "public_contract_preserved": "passed",
    }


def test_unsupported_language_is_unavailable_per_language_switch(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    ev = cv.verify_candidate(repo_path=tmp_path, patch_text=_PATCH, language="python",
                             test_project="tests/whatever")
    # per-language test-runner switch: unvalidated languages honestly abstain
    assert ev["status"] == "unavailable"
    assert ev["verified_patch_hash"] == hashlib.sha256(_PATCH.encode("utf-8")).hexdigest()


def test_javascript_covering_test_uses_backend(tmp_path, monkeypatch):
    seen = {}

    class FakeBackend:
        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            seen.update({"language": spec.language, "project": project, "filter": test_filter})
            return SimpleNamespace(
                available=True, ran=True, passed=True, exit_code=0, error_summary="",
                duration_seconds=0.1, tests_selected=3,
            )

    monkeypatch.setattr(cv.backends, "get_backend", lambda language: FakeBackend())
    ev = cv.verify_candidate(
        repo_path=tmp_path, patch_text=_PATCH, language="typescript",
        test_project="src/foo.test.ts", test_filter=None,
    )

    assert ev["status"] == "passed"
    assert seen == {"language": "typescript", "project": "src/foo.test.ts", "filter": None}


_TS_RENAME_PATCH = (
    "diff --git a/src/public.ts b/src/public.ts\n"
    "--- a/src/public.ts\n"
    "+++ b/src/public.ts\n"
    "@@ -1 +1 @@\n"
    "-export function oldName(): void {}\n"
    "+export function newName(): void {}\n"
)


def test_typescript_removed_export_fails_even_when_public_tests_pass(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "public.ts").write_text(
        "export function newName(): void {}\n", encoding="utf-8"
    )

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_TS_RENAME_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
    )

    assert ev["status"] == "failed"
    assert ev["checks"]["public_contract_preserved"] == "failed"
    assert "public_contract_preserved" in ev["required_checks"]
    assert "oldName" in ev["reason"]


def test_typescript_compatibility_alias_passes_public_contract_check(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "public.ts").write_text(
        "export function newName(): void {}\n"
        "export { newName as oldName };\n",
        encoding="utf-8",
    )

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_TS_RENAME_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
    )

    assert ev["status"] == "passed"
    assert ev["checks"]["public_contract_preserved"] == "passed"
    assert ev["required_checks"] == ["covering_tests", "public_contract_preserved"]


def test_typescript_callable_const_alias_passes_public_contract_check(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "public.ts").write_text(
        "export function newName(): void {}\n"
        "export const oldName = newName;\n",
        encoding="utf-8",
    )

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_TS_RENAME_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
    )

    assert ev["status"] == "passed"
    assert ev["checks"]["public_contract_preserved"] == "passed"


def test_typescript_callable_wrapper_does_not_claim_signature_equivalence(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "public.ts").write_text(
        "export function newName(): void {}\n"
        "export const oldName = (...args: unknown[]) => newName(...args);\n",
        encoding="utf-8",
    )

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_TS_RENAME_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
    )

    assert ev["status"] == "failed"
    assert ev["checks"]["public_contract_preserved"] == "failed"


def test_typescript_alias_with_incompatible_signature_fails_contract_check(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "public.ts").write_text(
        "export function newName(value: string): void {}\n"
        "export { newName as oldName };\n",
        encoding="utf-8",
    )

    ev = cv.verify_candidate(
        repo_path=tmp_path,
        patch_text=_TS_RENAME_PATCH,
        language="typescript",
        test_project="src/public.test.ts",
    )

    assert ev["status"] == "failed"
    assert "signature" in ev["reason"]


def test_candidate_timeout_is_forwarded_to_the_backend_spec(tmp_path, monkeypatch):
    seen = {}

    class FakeBackend:
        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            seen["timeout"] = spec.command_timeout
            return SimpleNamespace(
                available=True, ran=True, passed=True, exit_code=0, error_summary="",
                duration_seconds=0.1, tests_selected=1,
            )

    monkeypatch.setattr(cv.backends, "get_backend", lambda _language: FakeBackend())
    ev = cv.verify_candidate(
        repo_path=tmp_path, patch_text=_PATCH, language="csharp",
        test_project="tests/A.csproj", timeout=123,
    )

    assert ev["status"] == "passed"
    assert seen["timeout"] == 123


def test_candidate_subprocess_timeout_fails_closed(tmp_path, monkeypatch):
    class FakeBackend:
        def run_tests(self, repo_root, spec, *, project=None, test_filter=None):
            raise subprocess.TimeoutExpired(["test"], timeout=spec.command_timeout)

    monkeypatch.setattr(cv.backends, "get_backend", lambda _language: FakeBackend())
    ev = cv.verify_candidate(
        repo_path=tmp_path, patch_text=_PATCH, language="csharp",
        test_project="tests/A.csproj", timeout=2,
    )

    assert ev["status"] == "unavailable"
    assert "timed out" in ev["reason"]
