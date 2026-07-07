"""Candidate verifier for the ``pebra_graph_repair`` arm.

When PEBRA blocks a risky patch with ``revise_safer`` and the subject resubmits a narrower candidate,
this runs the candidate's COVERING TESTS and emits the evidence PEBRA's gate 7 consumes. It is the
"tests as the bridge to correctness" seam: PEBRA never certifies the math itself; it requires a green
run of the tests that cover the change, bound to the exact candidate patch.

Design constraints:
- No ``import pebra`` (e2e reaches PEBRA only via the CLI). ``verified_patch_hash`` is the documented
  wire convention (sha256 hexdigest of the exact UTF-8 patch text) recomputed here; it MUST equal
  ``pebra.core.decision_engine.candidate_patch_hash`` so the engine's patch-binding check passes.
- Per-language test-runner switch: only ``csharp`` (``dotnet test``) is validated. Other languages
  abstain honestly (``unavailable``) rather than fabricate a pass.
- Fail-safe: absent SDK, absent covering tests, or an unsupported language all yield ``unavailable``
  (never ``passed``), so PEBRA keeps the write blocked instead of proceeding on nothing.

CONTRACT (caller-enforced, NOT checked here): ``repo_path`` must be the materialization of exactly
``patch_text`` — the caller applies ``patch_text`` to a scratch clone before calling. This verifier
hashes ``patch_text`` and runs the covering tests against ``repo_path``; it does NOT re-derive the
repo's actual diff, so if the caller materializes a DIFFERENT patch than the one it hashes, the green
run is bound to the wrong patch and the binding is a lie. The robust way to honor this contract is for
the caller to derive ``patch_text`` FROM ``repo_path``'s own diff (e.g. ``git diff``) rather than pass
an independent string. This module is not yet wired to a live caller; when it is, that caller owns the
patch↔repo consistency guarantee.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from e2e.external.utils import dotnet_harness as dn

_SUPPORTED_LANGUAGES = frozenset({"csharp"})
_COVERING_CHECK = "covering_tests"
_DOMAIN = "covering_tests"


def candidate_patch_hash(patch: str) -> str:
    """The wire convention binding a verification to its patch. MUST match
    ``pebra.core.decision_engine.candidate_patch_hash``: sha256 hexdigest of the exact UTF-8 patch
    text, no normalization."""
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def _evidence(status: str, *, patch_hash: str, checks: dict[str, str] | None = None,
              reason: str | None = None) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "status": status,
        "checks": dict(checks or {}),
        "required_checks": [_COVERING_CHECK],
        "domain": _DOMAIN,
        "verified_patch_hash": patch_hash,
    }
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
    if not test_project:
        return _evidence("unavailable", patch_hash=patch_hash,
                         reason="no covering tests declared for this candidate")

    result = dn.run_tests(
        Path(repo_path), sln=build_solution, project=test_project, test_filter=test_filter,
        timeout=timeout)
    if not result.available or not result.ran:
        return _evidence("unavailable", patch_hash=patch_hash,
                         reason=result.error_summary or "covering-test run did not execute")

    status = "passed" if result.passed else "failed"
    return _evidence(status, patch_hash=patch_hash, checks={_COVERING_CHECK: status},
                     reason=None if result.passed else (result.error_summary or "covering tests failed"))
