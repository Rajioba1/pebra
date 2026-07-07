"""Candidate verifier: run a revised candidate's covering tests and emit the evidence PEBRA's
gate 7 consumes (status + required_checks + verified_patch_hash). No `import pebra` (the hash is the
documented wire convention, recomputed here); dotnet is monkeypatched so these stay pure."""

from __future__ import annotations

import hashlib

from e2e.experiments.agent_ab.tools import candidate_verifier as cv
from e2e.external.utils import dotnet_harness as dn

_PATCH = (
    "diff --git a/src/Numerics/SpecialFunctions/Gamma.cs b/src/Numerics/SpecialFunctions/Gamma.cs\n"
    "--- a/src/Numerics/SpecialFunctions/Gamma.cs\n"
    "+++ b/src/Numerics/SpecialFunctions/Gamma.cs\n"
    "@@ -1,2 +1,2 @@\n-old\n+narrowed\n"
)


def _stub_tests(monkeypatch, *, available=True, ran=True, passed=True, selected=7):
    def fake_run_tests(repo_root, sln="TemplateBlueprint.sln", *, project=None, test_filter=None,
                       timeout=600):
        return dn.DotNetTestResult(
            available=available, ran=ran, passed=passed, exit_code=0 if passed else 1,
            error_summary="" if passed else "GammaTests.SomeCase FAILED", duration_seconds=0.1,
            tests_selected=selected)
    monkeypatch.setattr(dn, "run_tests", fake_run_tests)


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


def test_unsupported_language_is_unavailable_per_language_switch(tmp_path, monkeypatch):
    _stub_tests(monkeypatch, passed=True)
    ev = cv.verify_candidate(repo_path=tmp_path, patch_text=_PATCH, language="python",
                             test_project="tests/whatever")
    # per-language test-runner switch: only csharp/dotnet is validated; others honestly abstain
    assert ev["status"] == "unavailable"
    assert ev["verified_patch_hash"] == hashlib.sha256(_PATCH.encode("utf-8")).hexdigest()
