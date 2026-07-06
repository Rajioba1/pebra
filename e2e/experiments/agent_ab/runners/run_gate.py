"""The single, fail-closed execution gate for the agent-A/B experiment.

Running a real coding agent costs tokens and mutates clones, so it must be impossible to trigger by
accident. ``check_gate()`` is called as the first line of both ``run_pair._invoke_subject_agent`` and
``orchestrator.main`` — redundant by design, so even a direct import+call is gated.

Three conditions, ALL required:
  - E2E_AB_RUN=1          explicit opt-in unique to this experiment (nothing else sets it)
  - E2E_EXTERNAL=1        the existing external-repo gate (clones the real C# repo)
  - provider key          ANTHROPIC_API_KEY by default, or DEEPSEEK_API_KEY when
                          E2E_AB_PROVIDER=deepseek

Fail-closed: ``check_gate`` RAISES ``RunGateError`` (never silently continues) when the gate is shut.
Pure stdlib; no pebra import; no anthropic import.
"""

from __future__ import annotations

import os


class RunGateError(RuntimeError):
    """Raised when the A/B experiment run gate is not fully open."""


def _provider() -> str:
    return os.environ.get("E2E_AB_PROVIDER", "anthropic").strip().lower() or "anthropic"


def _api_key_env() -> str:
    return "DEEPSEEK_API_KEY" if _provider() == "deepseek" else "ANTHROPIC_API_KEY"


def _gate_open() -> bool:
    return (
        os.environ.get("E2E_AB_RUN") == "1"
        and os.environ.get("E2E_EXTERNAL") == "1"
        and bool(os.environ.get(_api_key_env()))
    )


def check_gate() -> None:
    """Raise RunGateError unless E2E_AB_RUN=1, E2E_EXTERNAL=1, and the provider key is set."""
    missing = []
    if os.environ.get("E2E_AB_RUN") != "1":
        missing.append("E2E_AB_RUN=1")
    if os.environ.get("E2E_EXTERNAL") != "1":
        missing.append("E2E_EXTERNAL=1")
    key_env = _api_key_env()
    if not os.environ.get(key_env):
        missing.append(f"{key_env}=<key>")
    if missing:
        raise RunGateError(
            "agent-A/B run gate is closed; refusing to run a real agent. Missing: "
            + ", ".join(missing)
        )
