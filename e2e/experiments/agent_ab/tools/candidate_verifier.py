"""Candidate verifier for the ``pebra_graph_repair`` arm.

When PEBRA blocks a risky patch with ``revise_safer`` and the subject resubmits a narrower candidate,
this runs the candidate's COVERING TESTS and emits the evidence PEBRA's gate 7 consumes. It is the
"tests as the bridge to correctness" seam: PEBRA never certifies the math itself; it requires a green
run of the tests that cover the change, bound to the exact candidate patch.

Design constraints:
- No ``import pebra`` (e2e reaches PEBRA only via the CLI). ``verified_patch_hash`` is the documented
  wire convention (sha256 hexdigest of the exact UTF-8 patch text) recomputed here; it MUST equal
  ``pebra.core.decision_engine.candidate_patch_hash`` so the engine's patch-binding check passes.
- Per-language test-runner switch: validated e2e backends only. Unsupported languages abstain honestly
  (``unavailable``) rather than fabricate a pass.
- Fail-safe: absent SDK, absent covering tests without an explicit build-fallback, or an unsupported
  language all yield ``unavailable`` (never ``passed``), so PEBRA keeps the write blocked instead of
  proceeding on nothing. Build fallback is opt-in and used for specimens whose public verifier is a
  build/typecheck profile rather than a test project.

CONTRACT (caller-enforced, checked at the runner boundary): ``repo_path`` must be the materialization
of exactly ``patch_text`` and its covering tests must be derived from the patch's touched file(s), not
from a subject-declared target. This verifier hashes ``patch_text`` and runs the selected covering
tests against ``repo_path``; the live graph-repair runner owns the patch↔repo↔coverage consistency
guarantee before calling this module.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import time
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab import backends
from e2e.experiments.agent_ab.tools import public_contract_verifier

_SUPPORTED_LANGUAGES = frozenset({"csharp", "javascript", "typescript"})
_COVERING_CHECK = "covering_tests"
_BUILD_CHECK = "candidate_build"
_DOMAIN = "covering_tests"
_VERIFY_COVERING_CHECK = "run targeted tests for the touched scope before commit"


def candidate_patch_hash(patch: str) -> str:
    """The wire convention binding a verification to its patch. MUST match
    ``pebra.core.decision_engine.candidate_patch_hash``: sha256 hexdigest of the exact UTF-8 patch
    text, no normalization."""
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def completed_checks_for_verify(evidence: dict[str, Any]) -> dict[str, str]:
    """Translate host-verifier checks to the production post-edit guidance vocabulary."""
    completed: dict[str, str] = {}
    checks = evidence.get("checks")
    for check, raw_status in (checks.items() if isinstance(checks, dict) else ()):
        status = str(raw_status).lower()
        if status not in {"passed", "failed"}:
            continue
        name = _VERIFY_COVERING_CHECK if check == _COVERING_CHECK else str(check)
        completed[name] = status
    return completed


def _evidence(
    status: str,
    *,
    patch_hash: str,
    checks: dict[str, str] | None = None,
    required_checks: list[str] | None = None,
    domain: str = _DOMAIN,
    reason: str | None = None,
    test_project: str | None = None,
    test_filter: str | None = None,
    tests_selected: int | None = None,
) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "status": status,
        "checks": dict(checks or {}),
        "required_checks": list(required_checks or [_COVERING_CHECK]),
        "domain": domain,
        "verified_patch_hash": patch_hash,
    }
    provenance: dict[str, Any] = {}
    if test_project is not None:
        provenance["test_project"] = test_project
    if test_filter is not None:
        provenance["test_filter"] = test_filter
    if tests_selected is not None:
        provenance["tests_selected"] = tests_selected
    if provenance:
        ev["provenance"] = provenance
    if reason is not None:
        ev["reason"] = reason
    return ev


def verify_candidate(
    *,
    repo_path: Path | str,
    patch_text: str,
    language: str = "csharp",
    test_project: str | None = None,
    test_filter: str | None = None,
    build_solution: str = "TemplateBlueprint.sln",
    harness_id: str | None = None,
    build_profile: str = "default",
    build_selector: str | None = None,
    allow_build_fallback: bool = False,
    required_checks: tuple[str, ...] = (),
    timeout: int = 600,
) -> dict[str, Any]:
    """Run the candidate's covering tests and return gate-7 evidence bound to ``patch_text``.

    Returns a plain dict (goes into the request evidence block as ``candidate_verification``) with
    ``status`` in {passed, failed, unavailable}. ``verified_patch_hash`` is always populated so the
    binding holds regardless of outcome."""
    patch_hash = candidate_patch_hash(patch_text)
    deadline = time.monotonic() + max(1, timeout)
    supported_required = {_BUILD_CHECK, "public_contract_preserved"}
    unknown_required = sorted(set(required_checks) - supported_required)
    if unknown_required:
        return _evidence(
            "unavailable", patch_hash=patch_hash, required_checks=list(required_checks),
            reason="unsupported required candidate check(s): " + ", ".join(unknown_required),
        )

    if language not in _SUPPORTED_LANGUAGES:
        return _evidence("unavailable", patch_hash=patch_hash,
                         reason=f"no validated test runner for language {language!r}")
    try:
        backend = backends.get_backend(language)
    except ValueError:
        return _evidence("unavailable", patch_hash=patch_hash,
                         reason=f"no validated test runner for language {language!r}")
    contract_requested = "public_contract_preserved" in required_checks
    contract_required = False
    if language in {"javascript", "typescript"}:
        contract_status, _contract_failures, contract_reason = (
            public_contract_verifier.check_typescript_public_contract(repo_path, patch_text)
        )
        if contract_status in {"failed", "unavailable"}:
            return _evidence(
                contract_status,
                patch_hash=patch_hash,
                checks={"public_contract_preserved": contract_status},
                required_checks=["public_contract_preserved"],
                domain="public_contract",
                reason=contract_reason,
            )
        contract_required = contract_status == "passed"
        if contract_requested and not contract_required:
            return _evidence(
                "unavailable",
                patch_hash=patch_hash,
                checks={"public_contract_preserved": "unavailable"},
                required_checks=["public_contract_preserved"],
                domain="public_contract",
                reason="required public-contract preservation check was not applicable",
            )
    elif contract_requested:
        return _evidence(
            "unavailable", patch_hash=patch_hash,
            checks={"public_contract_preserved": "unavailable"},
            required_checks=["public_contract_preserved"], domain="public_contract",
            reason=f"no validated public-contract verifier for language {language!r}",
        )

    spec = type("_Spec", (), {
        "language": language,
        "harness_id": harness_id or language,
        "build_solution": build_solution,
        "build_profile": build_profile,
        "build_selector": build_selector,
        "command_timeout": timeout,
    })()

    def _refresh_timeout() -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        spec.command_timeout = max(1, math.ceil(remaining))
        return True

    checks: dict[str, str] = {}
    required: list[str] = []
    if contract_required:
        checks["public_contract_preserved"] = "passed"

    def _with_contract(values: list[str]) -> list[str]:
        return [*values, "public_contract_preserved"] if contract_required else list(values)
    if _BUILD_CHECK in required_checks:
        if not _refresh_timeout():
            return _evidence(
                "unavailable", patch_hash=patch_hash, checks=checks,
                required_checks=_with_contract([_BUILD_CHECK]),
                domain=_BUILD_CHECK, reason="candidate verification exhausted its timeout budget",
            )
        try:
            build = backend.run_build(Path(repo_path), spec)
        except subprocess.TimeoutExpired:
            return _evidence(
                "unavailable", patch_hash=patch_hash, checks=checks,
                required_checks=_with_contract([_BUILD_CHECK]),
                domain=_BUILD_CHECK, reason=f"candidate build timed out after {timeout} seconds",
            )
        if not build.available or not build.ran:
            return _evidence(
                "unavailable", patch_hash=patch_hash, checks=checks,
                required_checks=_with_contract([_BUILD_CHECK]),
                domain=_BUILD_CHECK,
                reason=build.error_summary or "candidate build did not execute",
            )
        build_status = "passed" if build.passed else "failed"
        checks[_BUILD_CHECK] = build_status
        required.append(_BUILD_CHECK)
        if not build.passed:
            return _evidence(
                "failed", patch_hash=patch_hash, checks=checks,
                required_checks=_with_contract(required), domain=_BUILD_CHECK,
                reason=build.error_summary or "candidate build failed",
            )
    if not test_project:
        if _BUILD_CHECK in checks:
            return _evidence(
                "passed", patch_hash=patch_hash, checks=checks,
                required_checks=_with_contract(required), domain=_BUILD_CHECK,
            )
        if not allow_build_fallback:
            return _evidence("unavailable", patch_hash=patch_hash,
                             reason="no covering tests declared for this candidate")
        if not _refresh_timeout():
            return _evidence(
                "unavailable", patch_hash=patch_hash, checks=checks,
                required_checks=_with_contract([_BUILD_CHECK]),
                domain=_BUILD_CHECK, reason="candidate verification exhausted its timeout budget",
            )
        try:
            result = backend.run_build(Path(repo_path), spec)
        except subprocess.TimeoutExpired:
            return _evidence(
                "unavailable", patch_hash=patch_hash, checks=checks,
                required_checks=_with_contract([_BUILD_CHECK]),
                domain=_BUILD_CHECK, reason=f"candidate build timed out after {timeout} seconds",
            )
        if not result.available or not result.ran:
            return _evidence(
                "unavailable",
                patch_hash=patch_hash,
                checks=checks,
                required_checks=_with_contract([_BUILD_CHECK]),
                domain=_BUILD_CHECK,
                reason=result.error_summary or "candidate build did not execute",
            )
        status = "passed" if result.passed else "failed"
        checks[_BUILD_CHECK] = status
        required = [_BUILD_CHECK]
        return _evidence(
            status,
            patch_hash=patch_hash,
            checks=checks,
            required_checks=_with_contract(required),
            domain=_BUILD_CHECK,
            reason=None if result.passed else (result.error_summary or "candidate build failed"),
        )

    if not _refresh_timeout():
        checks[_COVERING_CHECK] = "unavailable"
        required.append(_COVERING_CHECK)
        return _evidence(
            "unavailable", patch_hash=patch_hash, checks=checks,
            required_checks=_with_contract(required),
            reason="candidate verification exhausted its timeout budget",
            test_project=test_project, test_filter=test_filter,
        )
    try:
        result = backend.run_tests(
            Path(repo_path), spec, project=test_project, test_filter=test_filter
        )
    except subprocess.TimeoutExpired:
        checks[_COVERING_CHECK] = "unavailable"
        required.append(_COVERING_CHECK)
        return _evidence(
            "unavailable", patch_hash=patch_hash, checks=checks,
            required_checks=_with_contract(required),
            reason=f"candidate tests timed out after {timeout} seconds",
            test_project=test_project, test_filter=test_filter,
        )
    if not result.available or not result.ran:
        checks[_COVERING_CHECK] = "unavailable"
        required.append(_COVERING_CHECK)
        return _evidence("unavailable", patch_hash=patch_hash, checks=checks,
                         required_checks=_with_contract(required),
                         reason=result.error_summary or "covering-test run did not execute",
                         test_project=test_project, test_filter=test_filter,
                         tests_selected=result.tests_selected)
    if not isinstance(result.tests_selected, int) or result.tests_selected <= 0:
        checks[_COVERING_CHECK] = "unavailable"
        required.append(_COVERING_CHECK)
        return _evidence(
            "unavailable",
            patch_hash=patch_hash,
            checks=checks,
            required_checks=_with_contract(required),
            reason="covering-test run selected no tests",
            test_project=test_project,
            test_filter=test_filter,
            tests_selected=result.tests_selected,
        )

    status = "passed" if result.passed else "failed"
    checks[_COVERING_CHECK] = status
    required.append(_COVERING_CHECK)
    return _evidence(status, patch_hash=patch_hash, checks=checks,
                     required_checks=_with_contract(required),
                     reason=None if result.passed else (result.error_summary or "covering tests failed"),
                     test_project=test_project, test_filter=test_filter,
                     tests_selected=result.tests_selected)
