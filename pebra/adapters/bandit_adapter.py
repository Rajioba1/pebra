"""bandit_adapter (Slice 4c) — security evidence via bandit.

Adapter layer: bandit is allowed here (the import-linter forbids it in core/). Maps MEDIUM+ severity
findings on the CHANGED, non-test Python files into a single ``security_sensitive_change`` EVENT
(which is in CONSEQUENCE_BEARING_EVENTS, so the engine's criticality floor can apply to it).

Rules (ratified):
  - findings are EVIDENCE, not a gap — a clean or finding-bearing run carries no evidence_quality
    penalty; only an inability to RUN bandit lowers evidence_quality (an evidence gap, never fake
    safety);
  - bandit never sets criticality_stage — criticality stays policy/capability-driven;
  - caller-supplied paths are validated BEFORE bandit reads them (same escape class as RCA);
  - test files are excluded (a finding in a test helper is not a production security event).
"""

from __future__ import annotations

import json
import subprocess
import sys
from fnmatch import fnmatch

from pebra.adapters._paths import safe_relative_files

# Uncalibrated priors (AD-9 style): how likely a finding of each severity reflects a real
# security-sensitive change. LOW is intentionally absent — filtered below the MEDIUM threshold.
_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_SEVERITY_P_EVENT = {"MEDIUM": 0.08, "HIGH": 0.20}
_SECURITY_DISUTILITY = 0.80  # strong prior; the criticality floor still governs downstream
_BANDIT_UNAVAILABLE_PENALTY = 0.15  # evidence_quality penalty when bandit cannot run
_DEFAULT_TEST_PATTERNS = ("tests/**", "test_*.py", "*_test.py")


def _is_test_file(rel: str, patterns: tuple[str, ...]) -> bool:
    if rel.startswith("tests/") or "/tests/" in rel:
        return True
    base = rel.rsplit("/", 1)[-1]
    return any(fnmatch(rel, pat) or fnmatch(base, pat) for pat in patterns)


def _run_bandit(py: list[str], repo_root: str) -> dict | None:
    """Run bandit -f json on the given files. Returns the parsed report, or None if bandit could not
    run / its output was not parseable (an evidence gap). A non-zero exit just means findings exist."""
    try:
        res = subprocess.run(
            [sys.executable, "-m", "bandit", "-f", "json", "-q", "--", *py],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        report = json.loads(res.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    return report if isinstance(report, dict) else None


class BanditAdapter:
    def __init__(
        self, severity_threshold: str = "MEDIUM", test_file_patterns: list[str] | None = None
    ) -> None:
        self._min_rank = _SEVERITY_RANK.get(severity_threshold.upper(), 2)
        self._test_patterns = (
            tuple(test_file_patterns) if test_file_patterns is not None else _DEFAULT_TEST_PATTERNS
        )

    def gather_security_events(self, files: list[str], repo_root: str) -> tuple[list[dict], float]:
        """Returns (events, evidence_quality_penalty). penalty is 0.0 on any successful run (even with
        findings) and > 0.0 only when bandit could not run."""
        # reject escaping paths before any read; normalize separators so test-file exclusion and
        # bandit argv are deterministic across OSes (Windows backslash paths must still be excluded).
        safe = [p.replace("\\", "/") for p in safe_relative_files(repo_root, files)]
        py = [f for f in safe if f.endswith(".py") and not _is_test_file(f, self._test_patterns)]
        if not py:
            return [], 0.0  # nothing to scan -> no events, no gap
        report = _run_bandit(py, repo_root)
        if report is None:
            return [], _BANDIT_UNAVAILABLE_PENALTY  # could not run -> evidence gap, not fake safety
        relevant = [
            r
            for r in report.get("results", [])
            if _SEVERITY_RANK.get(str(r.get("issue_severity", "")).upper(), 0) >= self._min_rank
        ]
        if not relevant:
            return [], 0.0  # ran cleanly (no qualifying findings) -> not a gap
        # A ranked finding always carries weight: an uncalibrated high severity (e.g. a future
        # CRITICAL) falls back to the HIGH prior rather than a 0.0 no-op that would silently vanish.
        p_event = max(
            _SEVERITY_P_EVENT.get(str(r.get("issue_severity", "")).upper(), _SEVERITY_P_EVENT["HIGH"])
            for r in relevant
        )
        event = {
            "event": "security_sensitive_change",
            "p_event": p_event,
            "elicited_disutility": _SECURITY_DISUTILITY,
        }
        return [event], 0.0
