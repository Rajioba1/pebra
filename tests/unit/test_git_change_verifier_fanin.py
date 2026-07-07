"""A1 (M5c.5) — GitChangeVerifier fan-in enrichment: fill callers_percentile from the injected graph
lookup before reclassification, fail-soft when absent/erroring. Pure (no git)."""

from __future__ import annotations

from pebra.adapters.git_change_verifier import GitChangeVerifier


def test_enrich_fanin_fills_from_lookup() -> None:
    rows = [{"symbol_id": "a.py::f", "callers_percentile": 0.0},
            {"symbol_id": "a.py::g", "callers_percentile": 0.0}]
    GitChangeVerifier(fanin_lookup=lambda ids, root: {"a.py::f": 0.95})._enrich_fanin(rows, "/repo")
    assert rows[0]["callers_percentile"] == 0.95
    assert rows[1]["callers_percentile"] == 0.0  # not in lookup -> conservative 0.0 retained


def test_enrich_fanin_noop_without_lookup() -> None:
    rows = [{"symbol_id": "a.py::f", "callers_percentile": 0.0}]
    GitChangeVerifier()._enrich_fanin(rows, "/repo")
    assert rows[0]["callers_percentile"] == 0.0


def test_enrich_fanin_failsoft_on_lookup_error() -> None:
    def boom(ids, root):
        raise RuntimeError("graph engine blew up")

    rows = [{"symbol_id": "a.py::f", "callers_percentile": 0.0}]
    GitChangeVerifier(fanin_lookup=boom)._enrich_fanin(rows, "/repo")  # must not raise
    assert rows[0]["callers_percentile"] == 0.0


def test_reclassify_surfaces_consequential_from_high_fanin(monkeypatch) -> None:
    # Full chain inside _reclassify: enrich callers_percentile -> classify_diff -> consequential flag
    # surfaces in the return tuple. Stubs git + the AST diff so no real repo/parsing is needed.
    from pebra.adapters import git_change_verifier as gcv
    from pebra.adapters.ast_diff_adapter import _row

    behavioral = _row("a.py::Cls.m", "Cls.m", signature_changed=False, body_changed=True,
                      control_flow_changed=False)
    monkeypatch.setattr(gcv.git_adapter, "file_at_rev", lambda root, rev, f: "src")
    monkeypatch.setattr(gcv, "parses", lambda src: True)
    monkeypatch.setattr(gcv, "compute_complexity_delta", lambda b, a: 0.0)
    monkeypatch.setattr(gcv, "compute_symbol_diff_rows", lambda b, a, f: [dict(behavioral)])

    v = gcv.GitChangeVerifier(fanin_lookup=lambda ids, root: {"a.py::Cls.m": 0.97})
    max_kind, symbols, delta, analyzed, consequential, reasons, py_analyzed = (
        v._reclassify("/repo", ["a.py"], "x"))
    assert consequential is True  # high fan-in made a BEHAVIORAL change consequential
    assert py_analyzed is True    # a Python file parsed cleanly -> complexity delta is real
    assert any("callers_percentile" in r for r in reasons)

    # without the lookup, the same BEHAVIORAL change is NOT consequential (callers_percentile stays 0.0)
    v2 = gcv.GitChangeVerifier()
    assert v2._reclassify("/repo", ["a.py"], "x")[4] is False


def test_reclassify_non_python_uses_structural_symbols(monkeypatch) -> None:
    # Multi-language verify tier: a non-Python changed file is reclassified from graph structure
    # (exported owner -> coarse CONTRACT) instead of being silently skipped.
    from pebra.adapters import git_change_verifier as gcv
    from pebra.core.models import FanInEvidence

    monkeypatch.setattr(gcv.git_adapter, "file_at_rev", lambda root, rev, f: "src")

    def fake_structural(f, before, after, root):
        return FanInEvidence(
            resolution_method="location", graph_freshness="fresh",
            resolved_qualified_names=("Ns.Widget::Render",), resolved_symbol_count=1,
            node_ids_resolved=("cs:Render",), is_exported_contract=True)

    v = gcv.GitChangeVerifier(structural_symbols_fn=fake_structural)
    max_kind, symbols, delta, analyzed, conseq, reasons, py_analyzed = (
        v._reclassify("/repo", ["Widget.cs"], "x"))
    assert analyzed is True and py_analyzed is False  # reclassified, but no Python complexity delta
    assert max_kind == "CONTRACT"
    assert "Ns.Widget::Render" in symbols


def test_reclassify_non_python_skipped_without_structural_fn() -> None:
    # Pre-multilang behavior preserved: no structural lookup wired -> non-Python files are ignored.
    from pebra.adapters import git_change_verifier as gcv

    v = gcv.GitChangeVerifier()
    max_kind, symbols, delta, analyzed, conseq, reasons, py_analyzed = (
        v._reclassify("/repo", ["Widget.cs"], "x"))
    assert analyzed is False and max_kind == "UNKNOWN"


def test_reclassify_non_python_fresh_but_unresolved_fails_closed(monkeypatch) -> None:
    # A FRESH graph that resolves NO owner (e.g. a deleted in-scope .cs file) must count as an ATTEMPTED
    # reclassification (analyzed=True) so UNKNOWN + reclassification_attempted escalates the guardrail —
    # otherwise a dangerous in-scope non-Python change slips through verify.
    from pebra.adapters import git_change_verifier as gcv
    from pebra.core.models import FanInEvidence

    monkeypatch.setattr(gcv.git_adapter, "file_at_rev", lambda root, rev, f: "src")
    unresolved_fresh = FanInEvidence(resolution_method="unresolved", graph_freshness="fresh")
    v = gcv.GitChangeVerifier(structural_symbols_fn=lambda *a: unresolved_fresh)
    _kind, _syms, _d, analyzed, _c, _r, _py = v._reclassify("/repo", ["Widget.cs"], "x")
    assert analyzed is True  # fresh graph, no owner -> fail closed (attempted), not silently skipped


def test_reclassify_fresh_unresolved_structural_file_is_not_masked_by_python_cosmetic(
    monkeypatch,
) -> None:
    # A cleanly parsed no-row Python edit plus a fresh-but-unresolved structural file must stay UNKNOWN.
    # Otherwise the Python cosmetic branch masks a deleted/unresolved non-Python file in the same commit.
    from pebra.adapters import git_change_verifier as gcv
    from pebra.core.models import FanInEvidence

    monkeypatch.setattr(gcv.git_adapter, "file_at_rev", lambda root, rev, f: "src")
    monkeypatch.setattr(gcv, "parses", lambda src: True)
    monkeypatch.setattr(gcv, "compute_complexity_delta", lambda b, a: 0.0)
    monkeypatch.setattr(gcv, "compute_symbol_diff_rows", lambda b, a, f: [])
    unresolved_fresh = FanInEvidence(resolution_method="unresolved", graph_freshness="fresh")

    v = gcv.GitChangeVerifier(structural_symbols_fn=lambda *a: unresolved_fresh)
    kind, _syms, _d, analyzed, _c, _r, py_analyzed = (
        v._reclassify("/repo", ["doc.py", "Payment.cs"], "x")
    )

    assert kind == "UNKNOWN"
    assert analyzed is True
    assert py_analyzed is True


def test_reclassify_unparsable_python_is_not_masked_by_other_rows(monkeypatch) -> None:
    # If any parsed file produces rows, an unparsable Python file must still fail the whole envelope.
    from pebra.adapters import git_change_verifier as gcv
    from pebra.adapters.ast_diff_adapter import _row

    behavioral = _row("good.py::f", "f", signature_changed=False, body_changed=True,
                      control_flow_changed=False)
    sources = {"bad.py": "def broken(:\n", "good.py": "def f():\n    return 1\n"}
    monkeypatch.setattr(gcv.git_adapter, "file_at_rev", lambda root, rev, f: sources[f])
    monkeypatch.setattr(gcv.GitChangeVerifier, "_read_after",
                        lambda self, root, scope, f: sources[f])
    monkeypatch.setattr(gcv, "parses", lambda src: "broken" not in src)
    monkeypatch.setattr(gcv, "compute_complexity_delta", lambda b, a: 0.0)
    monkeypatch.setattr(gcv, "compute_symbol_diff_rows", lambda b, a, f: [dict(behavioral)])

    kind, _syms, _d, analyzed, _c, _r, py_analyzed = (
        gcv.GitChangeVerifier()._reclassify("/repo", ["bad.py", "good.py"], "x")
    )

    assert kind == "UNKNOWN"
    assert analyzed is True
    assert py_analyzed is True


def test_reclassify_non_source_file_does_not_use_structural_symbols(monkeypatch) -> None:
    # Graph structural verify is a code-owner check, not a generic file validator. A changed README or
    # config file must not become UNKNOWN+attempted merely because a fresh graph cannot resolve it.
    from pebra.adapters import git_change_verifier as gcv
    from pebra.core.models import FanInEvidence

    calls = []

    def fake_structural(*args):
        calls.append(args)
        return FanInEvidence(resolution_method="unresolved", graph_freshness="fresh")

    v = gcv.GitChangeVerifier(structural_symbols_fn=fake_structural)
    _kind, _syms, _d, analyzed, _c, _r, _py = v._reclassify("/repo", ["README.md"], "x")
    assert analyzed is False
    assert calls == []


def test_reclassify_non_python_absent_graph_does_not_force_escalation(monkeypatch) -> None:
    # But a merely ABSENT/stale graph (freshness != fresh) is infra absence, not a change signal — it
    # must NOT force every non-Python edit to escalate (codegraph is optional).
    from pebra.adapters import git_change_verifier as gcv
    from pebra.core.models import FanInEvidence

    monkeypatch.setattr(gcv.git_adapter, "file_at_rev", lambda root, rev, f: "src")
    unresolved_absent = FanInEvidence(resolution_method="unresolved", graph_freshness="unknown")
    v = gcv.GitChangeVerifier(structural_symbols_fn=lambda *a: unresolved_absent)
    _kind, _syms, _d, analyzed, _c, _r, _py = v._reclassify("/repo", ["Widget.cs"], "x")
    assert analyzed is False


def test_reclassify_uses_threshold_override(monkeypatch) -> None:
    from pebra.adapters import git_change_verifier as gcv
    from pebra.adapters.ast_diff_adapter import _row

    behavioral = _row("a.py::f", "f", signature_changed=False, body_changed=True,
                      control_flow_changed=False)
    monkeypatch.setattr(gcv.git_adapter, "file_at_rev", lambda root, rev, f: "src")
    monkeypatch.setattr(gcv, "parses", lambda src: True)
    monkeypatch.setattr(gcv, "compute_complexity_delta", lambda b, a: 0.0)
    monkeypatch.setattr(gcv, "compute_symbol_diff_rows", lambda b, a, f: [dict(behavioral)])

    v = gcv.GitChangeVerifier(fanin_lookup=lambda ids, root: {"a.py::f": 0.85})
    assert v._reclassify("/repo", ["a.py"], "x", thresholds={})[4] is False
    assert v._reclassify(
        "/repo", ["a.py"], "x", thresholds={"consequential_symbol_fan_in_percentile": 0.80}
    )[4] is True
