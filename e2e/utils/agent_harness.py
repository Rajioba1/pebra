"""Scripted agent — simulates a coding agent using PEBRA over the CLI boundary ONLY.

The agent: proposes/applies a risky edit within the approved safe scope, calls PEBRA to assess, runs
the post-edit verify (marking the binding's required checks done), records the terminal outcome, and
triggers learning. It NEVER imports pebra internals — everything is a subprocess through cli_harness.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from e2e.utils import cli_harness as ch

# The agent's edit — a plausible "harden validate_token" change, kept INSIDE the approved safe scope
# (only auth_service.py, only validate_token) so verify stays within the envelope.
_HARDENED_AUTH = '''# PEBRA e2e fixture — edited by the scripted agent (harden validate_token).
import hmac


def validate_token(token: str, secret: str) -> bool:
    """Validate a bearer token with a constant-time compare. SECURITY SENSITIVE."""
    return hmac.compare_digest(token, secret)


def revoke_all_sessions(user_id: str) -> None:
    """Revoke all active sessions for a user. Irreversible side effect."""
    raise NotImplementedError  # stub
'''


@dataclass
class AgentTranscript:
    assessment_id: str
    payload: dict
    verify_passed: bool
    learn_result: dict


def apply_risky_edit(repo_path: Path | str) -> None:
    """Apply the agent's edit and stage it (so verify --scope staged sees it)."""
    repo_path = Path(repo_path)
    (repo_path / "auth_service.py").write_text(_HARDENED_AUTH, encoding="utf-8")
    subprocess.run(["git", "add", "auth_service.py"], cwd=str(repo_path), check=True,
                   capture_output=True, text=True)


def reset_risky_edit(repo_path: Path | str) -> None:
    """Return the temp fixture repo to the committed pre-edit state."""
    repo_path = Path(repo_path)
    subprocess.run(
        ["git", "restore", "--staged", "--worktree", "auth_service.py"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
        text=True,
    )


def _binding_checks(payload: dict) -> tuple[dict[str, str], bool]:
    binding = payload["model_guidance_packet"]["binding"]
    return {
        str(check): "passed" for check in binding.get("required_checks_before_commit", [])
    }, bool(binding.get("requires_dry_run"))


def run_pre_edit_cycle(
    repo_path: Path | str, db_path: Path | str, request_path: Path | str, *, actual_success: bool,
) -> AgentTranscript:
    """One full agent cycle over the CLI boundary.

    PEBRA is a pre-edit assessment tool, so the order is:
    assess proposed edit -> apply edit -> verify actual diff -> record outcome -> learn.
    """
    payload = ch.assess(request_path, repo_root=repo_path, db=db_path)
    asm = payload["assessment_id"]
    apply_risky_edit(repo_path)
    checks, dry_run = _binding_checks(payload)
    passed, _ = ch.verify(asm, repo_root=repo_path, db=db_path, completed_checks=checks,
                          dry_run_preview=dry_run)
    if not passed:
        raise RuntimeError(f"verify did not PROCEED for {asm} — cannot record a completed outcome")
    ch.record_outcome(asm, "completed", repo_root=repo_path, db=db_path,
                      detail={"actual_success": actual_success})
    learn = ch.learn(asm, repo_root=repo_path, db=db_path)
    return AgentTranscript(assessment_id=asm, payload=payload, verify_passed=passed, learn_result=learn)


def run_cycle(
    repo_path: Path | str, db_path: Path | str, request_path: Path | str, *, actual_success: bool,
) -> AgentTranscript:
    """Backward-compatible name for the pre-edit cycle."""
    return run_pre_edit_cycle(repo_path, db_path, request_path, actual_success=actual_success)


def seed_failed_history(repo_path: Path | str, db_path: Path | str, request_path: Path | str, *, n: int) -> None:
    """Seed repeated completed outcomes through the real CLI workflow.

    Each sample starts from a clean pre-edit tree, assesses the proposed edit, applies it, verifies it,
    records the failed outcome, learns from it, then resets to clean for the next sample.
    """
    for _ in range(n):
        run_pre_edit_cycle(repo_path, db_path, request_path, actual_success=False)
        reset_risky_edit(repo_path)
