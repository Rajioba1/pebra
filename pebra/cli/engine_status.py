"""Read-only runtime engine status for doctor and capabilities."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from typing import Any

from pebra.adapters import git_adapter
from pebra.adapters.rca_adapter import probe_rca
from pebra.cli import setup_graph as sg
from pebra.core.graph_version import CODEGRAPH_ACCEPTED_RANGE, in_accepted_range


def _probe_bandit() -> tuple[bool, str | None]:
    """Verify the installed module entry point without scanning repository files."""
    try:
        version = importlib.metadata.version("bandit")
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return False, None
    try:
        result = subprocess.run(
            [sys.executable, "-m", "bandit", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False, version
    return result.returncode == 0, version


def collect_engine_status(repo_root: str) -> dict[str, Any]:
    """Probe Git, CodeGraph, RCA, and Bandit without installing or mutating."""
    head = git_adapter.head_commit(repo_root)
    git_row = {
        "mode": "available" if head else "degraded",
        "reasons": [] if head else ["HEAD unavailable"],
        "head_present": bool(head),
    }

    installed = sg._installed()
    version = sg._installed_version() if installed else None
    in_range = bool(version) and in_accepted_range(version)
    status = sg._status(repo_root) if installed else None
    status_version = status.get("version") if isinstance(status, dict) else None
    status_in_range = bool(status_version) and in_accepted_range(status_version)
    graph_config = sg._graph_config(repo_root)
    diag = sg._diagnosis(status) if installed else {
        "healthy": False, "initialized": False, "worktree_mismatch": False,
        "detail": "engine not found",
    }
    graph_row = {
        "mode": (
            "available"
            if (
                installed
                and in_range
                and status_in_range
                and graph_config["valid"]
                and diag.get("healthy")
            )
            else "degraded"
        ),
        "reasons": [],
        "installed": installed,
        "version": version,
        "version_in_range": in_range,
        "status_version": status_version,
        "status_version_in_range": status_in_range,
        "accepted_range": CODEGRAPH_ACCEPTED_RANGE,
        "config_valid": graph_config["valid"],
        "diagnosis": diag,
    }
    if not installed:
        graph_row["reasons"].append("codegraph not found")
    elif not in_range:
        graph_row["reasons"].append("version outside accepted range")
    elif not status_in_range:
        graph_row["reasons"].append("status version outside accepted range")
    elif not graph_config["valid"]:
        graph_row["reasons"].append(graph_config["error"] or "graph configuration invalid")
    elif not diag.get("healthy"):
        graph_row["reasons"].append(diag.get("detail") or "index not healthy")

    rca = probe_rca()
    rca_row = {
        "mode": "available" if rca.accepted else "degraded",
        "reasons": [] if rca.accepted else [rca.reason or rca.status],
        "status": rca.status,
        "accepted": rca.accepted,
        "version": rca.version,
        "benefit_mode": rca.benefit_mode,
        "accepted_version": rca.accepted_version,
        "required_source_revision": rca.required_source_revision,
        "sha256": rca.sha256,
        "source_revision": rca.source_revision,
        "validation_mode": rca.validation_mode,
    }

    bandit_ok, bandit_version = _probe_bandit()
    bandit_row = {
        "mode": "available" if bandit_ok else "degraded",
        "reasons": [] if bandit_ok else ["bandit module entry point unavailable"],
        "version": bandit_version,
        "validation_mode": "module_version_probe",
    }

    engines_ok = (
        git_row["mode"] == "available"
        and graph_row["mode"] == "available"
        and rca_row["mode"] == "available"
        and bandit_row["mode"] == "available"
    )
    return {
        "engines_ok": engines_ok,
        "git": git_row,
        "codegraph": graph_row,
        "rca": rca_row,
        "bandit": bandit_row,
    }
