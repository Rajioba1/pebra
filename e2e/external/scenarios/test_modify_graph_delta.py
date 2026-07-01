"""Scenario D — graph-backed MODIFY proof on a REAL C# repo.

The same public/consequential signature edit is cold-start green without graph evidence, but a fresh
CodeGraph index lets PEBRA see the changed symbol as a codebase-wide modify risk and boosts ordinary
expected-loss events. This proves MODIFY decisions are graph-aware, not only DELETE decisions.
"""

from __future__ import annotations

import json

from e2e.external.utils import signature_edit as se
from e2e.utils import cli_harness as ch


def _write_request(copy, dest):
    dest.write_text(json.dumps(se.build_signature_request(copy)), encoding="utf-8")
    return dest


def _event(payload, name):
    return next((e for e in payload["scores"]["loss_components"] if e["event"] == name), None)


def test_codegraph_changes_public_signature_modify_decision(indexed_copy, nograph_copy, nograph_env, tmp_path):
    graph_req = _write_request(indexed_copy, tmp_path / "signature_graph.json")
    nograph_req = _write_request(nograph_copy, tmp_path / "signature_nograph.json")

    graph = ch.assess(graph_req, repo_root=indexed_copy, db=tmp_path / "graph.db")
    nograph = ch.assess(
        nograph_req, repo_root=nograph_copy, db=tmp_path / "nograph.db", extra_env=nograph_env
    )

    g_sse = graph["scores"]["symbol_scope_evidence"]
    n_sse = nograph["scores"]["symbol_scope_evidence"]
    assert g_sse["file_operation_kind"] == "NONE"
    assert g_sse["symbol_fanin"]["graph_freshness"] == "fresh"
    assert g_sse["symbol_fanin"]["owner_kinds"]
    assert "max_owner_span_lines" in g_sse["symbol_fanin"]
    assert isinstance(g_sse["symbol_fanin"]["incoming_edge_counts"], dict)
    assert isinstance(g_sse["symbol_fanin"]["outgoing_edge_counts"], dict)
    assert n_sse["symbol_fanin"]["resolution_method"] == "unresolved"

    g_pub = _event(graph, "public_api_break")
    n_pub = _event(nograph, "public_api_break")
    assert g_pub is not None and n_pub is not None
    assert g_pub["p_event"] > n_pub["p_event"]
    assert _event(graph, "dependency_break") is not None
    assert _event(nograph, "dependency_break") is None
    assert _event(nograph, "api_contract_break") is None
    assert n_pub["p_event"] == 0.10

    assert nograph["recommended_decision"] == "proceed"
    assert graph["recommended_decision"] in {"ask_human", "reject"}
    assert graph["scores"]["expected_loss"] > graph["scores"]["effective_threshold"]
