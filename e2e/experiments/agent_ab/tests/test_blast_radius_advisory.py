"""blast_radius advisory backend — the CTXO-style positive control.

Lists the dependent files (from `pebra dependents`) in the advisory text, with NO risk verdict, in the
shared advisory shape (byte-identical keys to sham/pebra), and NO engine/experiment leak vocabulary.
The `pebra dependents` call is mocked so the backend logic is tested without the graph/binary.
"""

from __future__ import annotations

from e2e.experiments.agent_ab import forbidden
from e2e.experiments.agent_ab.tools import advisory_blast_radius, advisory_contract
from e2e.utils import cli_harness

_PAYLOAD = {"target_file": "src/a.cs", "change_summary": "add param", "proposed_patch": "--- diff ---"}


def _deps(files, *, available=True):
    return {
        "available": available,
        "graph_freshness": "fresh" if available else "unknown",
        "dependent_files": files,
        "count": len(files),
        "fallback_reason": None if available else "codegraph unavailable",
    }


def _no_leak(text: str) -> bool:
    return forbidden.match_terms(text, forbidden.EXPERIMENT_LEAK_TERMS) == ()


def test_lists_dependent_files_in_advisory_no_verdict(monkeypatch):
    monkeypatch.setattr(cli_harness, "dependents_result",
                        lambda t, *, repo_root: _deps(["src/b.cs", "src/c.cs"]))
    out = advisory_blast_radius.advise(_PAYLOAD, repo_root="/r", db="/d")
    assert out["recommended_decision"] is None and out["risk_level"] == "unknown"
    assert "src/b.cs" in out["advisory"] and "src/c.cs" in out["advisory"]


def test_output_key_set_identical_to_contract(monkeypatch):
    # blinding: same keys as sham/pebra, and detail stays {} (only the advisory TEXT differs by arm).
    monkeypatch.setattr(cli_harness, "dependents_result", lambda t, *, repo_root: _deps(["src/b.cs"]))
    out = advisory_blast_radius.advise(_PAYLOAD, repo_root="/r", db=None)
    assert tuple(out.keys()) == advisory_contract.OUTPUT_KEYS and out["detail"] == {}


def test_advisory_has_no_leak_vocab(monkeypatch):
    monkeypatch.setattr(
        cli_harness,
        "dependents_result",
        lambda t, *, repo_root: _deps(["src/TemplateBlueprint.Core/Contracts/IWorkspace.cs"]),
    )
    out = advisory_blast_radius.advise(_PAYLOAD, repo_root="/r", db=None)
    assert _no_leak(out["advisory"])  # file paths are fine; engine words are not


def test_missing_required_fields_is_arm_neutral(monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(cli_harness, "dependents_result", lambda t, *, repo_root: called.append(1) or _deps([]))
    out = advisory_blast_radius.advise({"target_file": "src/a.cs"}, repo_root="/r", db=None)  # missing 2 fields
    assert out["recommended_decision"] is None and out["risk_level"] == "unknown"
    assert not called  # dependents is NOT called when required fields are missing


def test_tool_failure_falls_back_arm_neutral(monkeypatch):
    def _boom(t, *, repo_root):
        raise cli_harness.CLIError("dependents blew up")
    monkeypatch.setattr(cli_harness, "dependents_result", _boom)
    out = advisory_blast_radius.advise(_PAYLOAD, repo_root="/r", db=None)
    assert out["recommended_decision"] is None and "unavailable" in out["advisory"].lower()
    assert _no_leak(out["advisory"])


def test_empty_dependents_message(monkeypatch):
    monkeypatch.setattr(cli_harness, "dependents_result", lambda t, *, repo_root: _deps([]))
    out = advisory_blast_radius.advise(_PAYLOAD, repo_root="/r", db=None)
    assert "no other files" in out["advisory"].lower() and _no_leak(out["advisory"])


def test_unavailable_dependents_does_not_claim_no_references(monkeypatch):
    monkeypatch.setattr(cli_harness, "dependents_result", lambda t, *, repo_root: _deps([], available=False))
    out = advisory_blast_radius.advise(_PAYLOAD, repo_root="/r", db=None)
    advisory = out["advisory"].lower()
    assert "no other files" not in advisory
    assert "unavailable" in advisory
