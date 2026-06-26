"""Slice 4c — BanditAdapter security evidence.

MEDIUM+ findings on changed, non-test files become a single `security_sensitive_change` event.
Findings are evidence (no evidence_quality penalty); only an inability to RUN bandit is a gap. Bandit
never sets criticality. Caller paths are validated before bandit reads them.
"""

from __future__ import annotations

from pebra.adapters import bandit_adapter as ba
from pebra.adapters.bandit_adapter import BanditAdapter

_EVAL = "def run(s):\n    return eval(s)\n"  # bandit B307 -> MEDIUM
_ASSERT = "def f(x):\n    assert x\n    return x\n"  # bandit B101 -> LOW
_SAFE = "def f(x):\n    return x + 1\n"


def _write(tmp_path, rel: str, content: str) -> str:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_safe_file_yields_no_events(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SAFE)
    events, penalty = BanditAdapter().gather_security_events(["m.py"], root)
    assert events == []
    assert penalty == 0.0


def test_eval_finding_produces_security_event(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _EVAL)
    events, penalty = BanditAdapter().gather_security_events(["m.py"], root)
    assert len(events) == 1
    assert events[0]["event"] == "security_sensitive_change"
    assert events[0]["p_event"] > 0.0
    assert penalty == 0.0  # a finding is evidence, not a gap


def test_low_severity_filtered_at_medium_threshold(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _ASSERT)
    events, penalty = BanditAdapter().gather_security_events(["m.py"], root)
    assert events == []  # B101 is LOW -> below MEDIUM threshold
    assert penalty == 0.0


def test_test_files_are_excluded(tmp_path) -> None:
    root = _write(tmp_path, "tests/test_x.py", _EVAL)
    events, penalty = BanditAdapter().gather_security_events(["tests/test_x.py"], root)
    assert events == []  # a finding in a test helper is not a production security event


def test_unsafe_path_is_rejected_before_scan(tmp_path) -> None:
    root = _write(tmp_path, "m.py", _SAFE)
    (tmp_path.parent / "outside.py").write_text(_EVAL, encoding="utf-8")  # must never be scanned
    events, penalty = BanditAdapter().gather_security_events(["../outside.py"], root)
    assert events == []
    assert penalty == 0.0


def test_no_python_files_no_events(tmp_path) -> None:
    root = _write(tmp_path, "README.md", "# hi\n")
    events, penalty = BanditAdapter().gather_security_events(["README.md"], root)
    assert events == []
    assert penalty == 0.0


def test_bandit_unavailable_is_evidence_gap(tmp_path, monkeypatch) -> None:
    root = _write(tmp_path, "m.py", _EVAL)
    monkeypatch.setattr(ba, "_run_bandit", lambda py, repo_root: None)
    events, penalty = BanditAdapter().gather_security_events(["m.py"], root)
    assert events == []  # no fake safety
    assert penalty > 0.0  # could not run -> evidence-quality gap


def test_unknown_high_severity_still_fires_with_high_prior(tmp_path, monkeypatch) -> None:
    # a ranked-but-uncalibrated severity (e.g. a future CRITICAL) must not silently vanish or emit
    # a p_event=0.0 no-op event; it falls back to at least the HIGH prior.
    root = _write(tmp_path, "m.py", _SAFE)
    monkeypatch.setattr(
        ba, "_run_bandit", lambda py, repo_root: {"results": [{"issue_severity": "CRITICAL"}]}
    )
    events, penalty = BanditAdapter().gather_security_events(["m.py"], root)
    assert len(events) == 1
    assert events[0]["p_event"] >= 0.20


def test_backslash_test_path_is_excluded(tmp_path) -> None:
    # Windows-style separators must not let a test file slip past the exclusion.
    root = _write(tmp_path, "tests/test_x.py", _EVAL)
    events, penalty = BanditAdapter().gather_security_events(["tests\\test_x.py"], root)
    assert events == []


def test_high_severity_outranks_medium_in_dedup(tmp_path) -> None:
    # one event, p_event = the max severity prior across findings
    root = _write(
        tmp_path, "m.py", "import subprocess\ndef r(c):\n    subprocess.call(c, shell=True)\n"
    )
    events, penalty = BanditAdapter().gather_security_events(["m.py"], root)
    assert len(events) == 1
    assert events[0]["p_event"] >= 0.20  # shell=True is HIGH severity
