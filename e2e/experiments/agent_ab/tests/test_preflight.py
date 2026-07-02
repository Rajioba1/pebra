"""Pre-flight gates: pure assertion helpers for oracle-outcome and graph-freshness (mock injection)."""

from __future__ import annotations

from types import SimpleNamespace

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import preflight

_TRAP = TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)
_SAFE = TaskSpec("B1", "d", ("a.cs",), "safe", ("a.cs",), "none", False)


def _build(ran=True, passed=True, err=""):
    return SimpleNamespace(ran=ran, passed=passed, error_summary=err)


# ---- oracle-outcome assertions ----

def test_trap_that_fails_is_ok():
    assert preflight._oracle_failure(_TRAP, _build(passed=False, err="CS0535")) is None


def test_safe_that_passes_is_ok():
    assert preflight._oracle_failure(_SAFE, _build(passed=True)) is None


def test_trap_that_builds_is_flagged():
    msg = preflight._oracle_failure(_TRAP, _build(passed=True))
    assert msg and "MUST fail" in msg


def test_safe_that_fails_is_flagged():
    msg = preflight._oracle_failure(_SAFE, _build(passed=False, err="boom"))
    assert msg and "should pass" in msg


def test_build_did_not_run_is_flagged():
    assert "did not run" in preflight._oracle_failure(_TRAP, _build(ran=False))


# ---- graph-freshness assertions ----

def _payload(freshness, resolution):
    return {"scores": {"symbol_scope_evidence": {"symbol_fanin": {
        "graph_freshness": freshness, "resolution_method": resolution}}}}


def test_fresh_resolved_graph_is_ok():
    assert preflight._graph_backed_failure(_TRAP, _payload("fresh", "location")) is None
    assert preflight._graph_backed_failure(_TRAP, _payload("fresh", "name_fallback")) is None


def test_stale_graph_is_flagged():
    msg = preflight._graph_backed_failure(_TRAP, _payload("stale", "location"))
    assert msg and "not fresh" in msg


def test_unresolved_target_is_flagged():
    msg = preflight._graph_backed_failure(_TRAP, _payload("fresh", "unresolved"))
    assert msg and "did not resolve" in msg


def test_missing_evidence_is_flagged():
    assert preflight._graph_backed_failure(_TRAP, {}) is not None


# ---- accumulate-ALL-failures (never first-fail) ----

import pytest  # noqa: E402


def test_oracle_preflight_reports_all_missing_patches(tmp_path):
    corpus = [_TRAP, _SAFE]
    empty_patch_dir = tmp_path / "no_patches"
    empty_patch_dir.mkdir()
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_oracle_preflight(corpus, None, out_dir=tmp_path,
                                       build_fn=lambda p: _build(), patch_dir=empty_patch_dir)
    msg = str(ei.value)
    assert "T1" in msg and "B1" in msg  # BOTH reported, not first-fail


def test_oracle_preflight_accumulates_apply_failures(tmp_path, monkeypatch):
    corpus = [_TRAP, _SAFE]
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "T1.patch").write_text("bad")
    (patch_dir / "B1.patch").write_text("bad")
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)

    def _raise_apply(_pf, _rp):
        raise preflight.PreflightError("git apply failed")

    monkeypatch.setattr(preflight, "_apply_patch", _raise_apply)
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_oracle_preflight(corpus, None, out_dir=tmp_path,
                                       build_fn=lambda p: _build(), patch_dir=patch_dir)
    msg = str(ei.value)
    assert "T1" in msg and "B1" in msg  # apply failures accumulated, not short-circuited


def test_graph_preflight_accumulates_infra_errors(tmp_path, monkeypatch):
    trap2 = TaskSpec("T2", "d", ("b.cs",), "risky", ("b.cs",), "build_failure", True)

    def _boom(ext, dest):
        raise RuntimeError("clone exploded")

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _boom)
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_graph_preflight([_TRAP, trap2], None, out_dir=tmp_path,
                                      assess_fn=lambda rp, sp: {}, setup_graph_fn=None,
                                      node_count_fn=lambda p: {"csharp_callable": 999})
    msg = str(ei.value)
    assert "T1" in msg and "T2" in msg and "infrastructure error" in msg


# ---- independent graph-validity (node-count) check ----

def _fresh_payload(rp, sp):
    return _payload("fresh", "location")


def test_graph_preflight_fails_on_low_node_count(tmp_path, monkeypatch):
    # a 'fresh' index that parsed almost no C# must be flagged even though freshness/resolution pass
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_graph_preflight([_TRAP], None, out_dir=tmp_path,
                                      assess_fn=_fresh_payload, setup_graph_fn=None,
                                      node_count_fn=lambda p: {"csharp_callable": 3})
    assert "C# callable nodes" in str(ei.value) and "T1" in str(ei.value)


def test_graph_preflight_passes_with_enough_nodes_and_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)
    # enough C# nodes AND a fresh/resolved target -> no PreflightError raised
    preflight.run_graph_preflight([_TRAP], None, out_dir=tmp_path,
                                  assess_fn=_fresh_payload, setup_graph_fn=None,
                                  node_count_fn=lambda p: {"csharp_callable": 700})
