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
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab import backends

_SUPPORTED_LANGUAGES = frozenset({"csharp", "javascript", "typescript"})
_COVERING_CHECK = "covering_tests"
_BUILD_CHECK = "candidate_build"
_DOMAIN = "covering_tests"


def candidate_patch_hash(patch: str) -> str:
    """The wire convention binding a verification to its patch. MUST match
    ``pebra.core.decision_engine.candidate_patch_hash``: sha256 hexdigest of the exact UTF-8 patch
    text, no normalization."""
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


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
    timeout: int = 600,
) -> dict[str, Any]:
    """Run the candidate's covering tests and return gate-7 evidence bound to ``patch_text``.

    Returns a plain dict (goes into the request evidence block as ``candidate_verification``) with
    ``status`` in {passed, failed, unavailable}. ``verified_patch_hash`` is always populated so the
    binding holds regardless of outcome."""
    patch_hash = candidate_patch_hash(patch_text)

    if language not in _SUPPORTED_LANGUAGES:
        return _evidence("unavailable", patch_hash=patch_hash,
                         reason=f"no validated test runner for language {language!r}")
    try:
        backend = backends.get_backend(language)
    except ValueError:
        return _evidence("unavailable", patch_hash=patch_hash,
                         reason=f"no validated test runner for language {language!r}")
    spec = type("_Spec", (), {
        "language": language,
        "harness_id": harness_id or language,
        "build_solution": build_solution,
        "build_profile": build_profile,
        "build_selector": build_selector,
    })()
    if not test_project:
        if not allow_build_fallback:
            return _evidence("unavailable", patch_hash=patch_hash,
                             reason="no covering tests declared for this candidate")
        result = backend.run_build(Path(repo_path), spec)
        if not result.available or not result.ran:
            return _evidence(
                "unavailable",
                patch_hash=patch_hash,
                required_checks=[_BUILD_CHECK],
                domain=_BUILD_CHECK,
                reason=result.error_summary or "candidate build did not execute",
            )
        status = "passed" if result.passed else "failed"
        return _evidence(
            status,
            patch_hash=patch_hash,
            checks={_BUILD_CHECK: status},
            required_checks=[_BUILD_CHECK],
            domain=_BUILD_CHECK,
            reason=None if result.passed else (result.error_summary or "candidate build failed"),
        )

    result = backend.run_tests(Path(repo_path), spec, project=test_project, test_filter=test_filter)
    if not result.available or not result.ran:
        return _evidence("unavailable", patch_hash=patch_hash,
                         reason=result.error_summary or "covering-test run did not execute",
                         test_project=test_project, test_filter=test_filter,
                         tests_selected=result.tests_selected)

    status = "passed" if result.passed else "failed"
    return _evidence(status, patch_hash=patch_hash, checks={_COVERING_CHECK: status},
                     reason=None if result.passed else (result.error_summary or "covering tests failed"),
                     test_project=test_project, test_filter=test_filter,
                     tests_selected=result.tests_selected)
