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
    max_kind, symbols, delta, analyzed, consequential, reasons = v._reclassify("/repo", ["a.py"], "x")
    assert consequential is True  # high fan-in made a BEHAVIORAL change consequential
    assert any("callers_percentile" in r for r in reasons)

    # without the lookup, the same BEHAVIORAL change is NOT consequential (callers_percentile stays 0.0)
    v2 = gcv.GitChangeVerifier()
    assert v2._reclassify("/repo", ["a.py"], "x")[4] is False


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
