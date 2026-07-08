"""rca_adapter — the RCA benefit provider. Hermetic: an injected fake runner returns
canned FuncSpace JSON, so NO real binary is needed (the gated real-binary proof is a separate
integration test). Covers language gate, no-patch/unapplyable/changed-nothing -> projected, real repo
not mutated, fail-safe on a missing binary / bad JSON."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pebra.adapters import rca_adapter as ra
from pebra.adapters.rca_adapter import RustCodeAnalysisAdapter


class _FakeProc:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


_VALID_JSON = '{"metrics": {"cyclomatic": {"sum": 3.0}, "mi": {"mi_visual_studio": 80.0}}}'


# --- _run_rca_cli fail-safe branches (the REAL subprocess runner, monkeypatched) ----------------

def test_run_rca_cli_binary_missing_is_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ra, "find_rca", lambda: None)
    assert ra._run_rca_cli(tmp_path / "x.py") is None  # never spawns


def test_run_rca_cli_valid_json_parses(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ra, "find_rca", lambda: "rca")
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **k: _FakeProc(0, _VALID_JSON))
    assert ra._run_rca_cli(tmp_path / "x.py") == {
        "metrics": {"cyclomatic": {"sum": 3.0}, "mi": {"mi_visual_studio": 80.0}}
    }


def test_run_rca_cli_nonzero_returncode_is_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ra, "find_rca", lambda: "rca")
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **k: _FakeProc(1, _VALID_JSON))
    assert ra._run_rca_cli(tmp_path / "x.py") is None  # bad exit -> no trust even with JSON on stdout


def test_run_rca_cli_empty_stdout_is_none(monkeypatch, tmp_path) -> None:
    # THE load-bearing case: unsupported language (Kotlin/Go) exits 0 with empty stdout.
    monkeypatch.setattr(ra, "find_rca", lambda: "rca")
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **k: _FakeProc(0, "   \n"))
    assert ra._run_rca_cli(tmp_path / "x.kt") is None


def test_run_rca_cli_non_json_stdout_is_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ra, "find_rca", lambda: "rca")
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **k: _FakeProc(0, "not json {{{"))
    assert ra._run_rca_cli(tmp_path / "x.py") is None


def test_run_rca_cli_os_error_is_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ra, "find_rca", lambda: "rca")

    def _boom(*a, **k):
        raise OSError("cannot spawn")

    monkeypatch.setattr(ra.subprocess, "run", _boom)
    assert ra._run_rca_cli(tmp_path / "x.py") is None


def test_run_rca_cli_timeout_is_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ra, "find_rca", lambda: "rca")

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired("rca", 30)

    monkeypatch.setattr(ra.subprocess, "run", _timeout)
    assert ra._run_rca_cli(tmp_path / "x.py") is None


def _funcspace(cc: float, mi: float) -> dict:
    return {"kind": "unit", "metrics": {"cyclomatic": {"sum": cc}, "mi": {"mi_visual_studio": mi}}}


def _content_runner(path: Path) -> dict | None:
    """Content-sensitive fake: cc = count('if')+1, mi = len(source). Lets before/after differ."""
    src = path.read_text(encoding="utf-8")
    return _funcspace(float(src.count("if") + 1), float(len(src)))


def _none_runner(_path: Path) -> dict | None:
    return None


# --- _extract_metrics ---------------------------------------------------------------------------

def test_extract_metrics_from_valid_funcspace() -> None:
    assert ra._extract_metrics(_funcspace(3.0, 75.5)) == (3.0, 75.5)


def test_extract_metrics_missing_keys_is_none() -> None:
    assert ra._extract_metrics({"metrics": {"cyclomatic": {"sum": 1.0}}}) is None  # no mi
    assert ra._extract_metrics({}) is None
    assert ra._extract_metrics(None) is None
    assert ra._extract_metrics("not-a-dict") is None


# --- language gate ------------------------------------------------------------------------------

def test_supported_extensions() -> None:
    for ok in ("a.py", "a.js", "a.jsx", "a.ts", "a.tsx", "A.java", "a.rs", "a.c", "a.cpp"):
        assert ra._supported(ok), ok
    for no in ("a.kt", "a.go", "a.cs", "a.rb", "a.php", "a.scala", "a.dart", "Makefile"):
        assert not ra._supported(no), no


# --- measure_delta (post-edit / verify path) ----------------------------------------------------

def test_measure_delta_computes_signed_deltas() -> None:
    adapter = RustCodeAnalysisAdapter(runner=_content_runner)
    # before "if\nif\n" -> cc=3, mi=6 ; after "x\n" -> cc=1, mi=2
    d = adapter.measure_delta("a.py", "if\nif\n", "x\n")
    assert d == (1.0 - 3.0, 2.0 - 6.0)  # (cc_delta, mi_delta) = (-2, -4)


def test_measure_delta_unsupported_language_is_none() -> None:
    adapter = RustCodeAnalysisAdapter(runner=_content_runner)
    assert adapter.measure_delta("a.kt", "fun f(){}", "fun g(){}") is None


def test_measure_delta_none_side_is_none() -> None:
    adapter = RustCodeAnalysisAdapter(runner=_content_runner)
    assert adapter.measure_delta("a.py", None, "x") is None
    assert adapter.measure_delta("a.py", "x", None) is None


def test_measure_delta_runner_failure_is_none() -> None:
    adapter = RustCodeAnalysisAdapter(runner=_none_runner)  # binary "missing"
    assert adapter.measure_delta("a.py", "a", "b") is None


# --- gather_benefit_evidence (pre-edit path) ----------------------------------------------------

_PATCH = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,1 @@\n"
    "-def f(x):\n-    if x:\n-        return x\n+def f(x): return x\n"
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("def f(x):\n    if x:\n        return x\n", encoding="utf-8")
    return tmp_path


def test_gather_measured_when_patch_applies(tmp_path) -> None:
    repo = _repo(tmp_path)
    adapter = RustCodeAnalysisAdapter(runner=_content_runner)
    ev = adapter.gather_benefit_evidence(str(repo), ["a.py"], _PATCH)
    assert ev.source_type == "measured"
    assert set(ev.deltas) == {"complexity_delta", "maintainability_index_delta"}
    assert ev.scope == "a.py"


def test_gather_preserves_future_change_exposure(tmp_path) -> None:
    repo = _repo(tmp_path)
    ev = RustCodeAnalysisAdapter(runner=_content_runner).gather_benefit_evidence(
        str(repo), ["a.py"], _PATCH, future_change_exposure=0.7
    )
    assert ev.source_type == "measured"
    assert ev.future_change_exposure == 0.7


def test_gather_does_not_mutate_repo(tmp_path) -> None:
    repo = _repo(tmp_path)
    original = (repo / "a.py").read_text(encoding="utf-8")
    RustCodeAnalysisAdapter(runner=_content_runner).gather_benefit_evidence(str(repo), ["a.py"], _PATCH)
    assert (repo / "a.py").read_text(encoding="utf-8") == original  # patch applied only to a temp copy


def test_gather_no_patch_is_projected(tmp_path) -> None:
    ev = RustCodeAnalysisAdapter(runner=_content_runner).gather_benefit_evidence(str(_repo(tmp_path)), ["a.py"])
    assert ev.source_type == "projected" and ev.deltas == {}


def test_gather_no_supported_files_is_projected(tmp_path) -> None:
    (tmp_path / "a.kt").write_text("fun f(){}", encoding="utf-8")
    ev = RustCodeAnalysisAdapter(runner=_content_runner).gather_benefit_evidence(str(tmp_path), ["a.kt"], _PATCH)
    assert ev.source_type == "projected" and ev.deltas == {}


def test_gather_unapplyable_patch_is_projected(tmp_path) -> None:
    (tmp_path / "a.py").write_text("totally different content\n", encoding="utf-8")
    ev = RustCodeAnalysisAdapter(runner=_content_runner).gather_benefit_evidence(str(tmp_path), ["a.py"], _PATCH)
    assert ev.source_type == "projected" and ev.deltas == {}


def test_gather_binary_missing_is_projected(tmp_path) -> None:
    # runner returns None for every file (binary absent) -> no file measurable both sides -> projected
    ev = RustCodeAnalysisAdapter(runner=_none_runner).gather_benefit_evidence(str(_repo(tmp_path)), ["a.py"], _PATCH)
    assert ev.source_type == "projected" and ev.deltas == {}


def test_gather_rejects_path_escape(tmp_path) -> None:
    ev = RustCodeAnalysisAdapter(runner=_content_runner).gather_benefit_evidence(
        str(tmp_path), ["../evil.py", "C:/abs.py"], _PATCH)
    assert ev.source_type == "projected" and ev.deltas == {}  # escaping paths dropped -> no supported files


def test_gather_drops_unsafe_paths_but_measures_valid_file(tmp_path) -> None:
    repo = _repo(tmp_path)
    ev = RustCodeAnalysisAdapter(runner=_content_runner).gather_benefit_evidence(
        str(repo), ["../evil.py", "a.py"], _PATCH
    )
    assert ev.source_type == "measured"
    assert ev.scope == "a.py"
