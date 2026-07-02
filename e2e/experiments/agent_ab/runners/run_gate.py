"""The single, fail-closed execution gate for the agent-A/B experiment.

Running a real coding agent costs tokens and mutates clones, so it must be impossible to trigger by
accident. ``check_gate()`` is called as the first line of both ``run_pair._invoke_subject_agent`` and
``orchestrator.main`` — redundant by design, so even a direct import+call is gated.

Three conditions, ALL required:
  - E2E_AB_RUN=1          explicit opt-in unique to this experiment (nothing else sets it)
  - E2E_EXTERNAL=1        the existing external-repo gate (clones the real C# repo)
  - ANTHROPIC_API_KEY     non-empty; the live subject client needs credentials

Fail-closed: ``check_gate`` RAISES ``RunGateError`` (never silently continues) when the gate is shut.
Pure stdlib; no pebra import; no anthropic import.
"""

from __future__ import annotations

import os


class RunGateError(RuntimeError):
    """Raised when the A/B experiment run gate is not fully open."""


def _gate_open() -> bool:
    return (
        os.environ.get("E2E_AB_RUN") == "1"
        and os.environ.get("E2E_EXTERNAL") == "1"
        and bool(os.environ.get("ANTHROPIC_API_KEY"))
    )


def check_gate() -> None:
    """Raise RunGateError unless E2E_AB_RUN=1 AND E2E_EXTERNAL=1 AND ANTHROPIC_API_KEY is set."""
    missing = []
    if os.environ.get("E2E_AB_RUN") != "1":
        missing.append("E2E_AB_RUN=1")
    if os.environ.get("E2E_EXTERNAL") != "1":
        missing.append("E2E_EXTERNAL=1")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY=<key>")
    if missing:
        raise RunGateError(
            "agent-A/B run gate is closed; refusing to run a real agent. Missing: "
            + ", ".join(missing)
        )
