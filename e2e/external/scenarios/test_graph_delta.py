"""Scenario B — graph-backed destructive-op proof on a REAL C# repo.

Deleting a high-fan-in file (GridSearchAdapter.cs: 13 callers, percentile 1.0) must be escalated by
PEBRA *because CodeGraph saw the callers*. The request carries NO inline fan-in, so the file rollup can
only come from the graph. The proof is the graph-vs-no-graph DELTA: the same request, assessed on the
indexed copy vs a no-index clone, must show codegraph boosting the dependency_break event and pushing
expected_loss across the C3 risk budget.
"""

from __future__ import annotations

from pathlib import Path

from e2e.external.utils import delete_request as dr
from e2e.utils import report_generator as rg
from e2e.utils import cli_harness as ch


def test_codegraph_supplies_the_file_rollup(indexed_copy, tmp_path):
    req = dr.write_request(dr.build_delete_request(indexed_copy), tmp_path / "delete.json")
    payload = ch.assess(req, repo_root=indexed_copy, db=tmp_path / "graph.db")

    sse = payload["scores"]["symbol_scope_evidence"]
    assert sse["file_operation_kind"] == "DELETE"
    rollup = sse["file_fanin_rollup"]
    # the rollup came from the index, not the baseline floor:
    assert rollup["resolution_method"] == "file_location"
    assert rollup["graph_freshness"] == "fresh"
    # require the FULL graph depth (not a token >0): a stale/partial index resolving 1 caller at a low
    # percentile would otherwise pass while delivering a fraction of the claimed signal.
    assert rollup["distinct_caller_count"] >= 13  # this template_blueprint revision has 13 callers
    assert rollup["percentile"] >= 0.9            # spec: ~1.0 (margin for index variance)


def test_graph_vs_nograph_delta_escalates_risk(indexed_copy, nograph_copy, nograph_env, tmp_path):
    req = dr.write_request(dr.build_delete_request(indexed_copy), tmp_path / "delete.json")
    graph = ch.assess(req, repo_root=indexed_copy, db=tmp_path / "graph.db")
    assert not (nograph_copy / ".codegraph").exists()
    nograph = ch.assess(
        req, repo_root=nograph_copy, db=tmp_path / "nograph.db", extra_env=nograph_env
    )

    g_dep, n_dep = dr.dependency_break_p(graph), dr.dependency_break_p(nograph)
    assert g_dep is not None and n_dep is not None
    assert g_dep > n_dep  # CodeGraph's fan-in boosts the destructive-op event above the baseline floor

    g_loss = graph["scores"]["expected_loss"]
    n_loss = nograph["scores"]["expected_loss"]
    assert g_loss > n_loss
    # the boost is decision-relevant: with the graph, expected_loss EXCEEDS the C3 budget; without, it
    # stays under it. The graph is what blows the risk budget.
    assert g_loss > dr.C3_BUDGET >= n_loss

    # honest control: the no-index arm reports unresolved (no fabricated fan-in)
    no_rollup = nograph["scores"]["symbol_scope_evidence"]["file_fanin_rollup"]
    assert no_rollup["resolution_method"] == "unresolved"
    assert no_rollup["graph_freshness"] == "unknown"
    assert no_rollup["fallback_reason"]
    assert graph["recommended_decision"] in {"reject", "ask_human", "inspect_first"}

    report = rg.write_report(
        [
            rg.FeatureResult(
                "external_graph_delete_delta",
                "PASS",
                "codegraph",
                graph_evidence={
                    "engine": "CodeGraph",
                    "freshness": graph["scores"]["symbol_scope_evidence"]["file_fanin_rollup"][
                        "graph_freshness"
                    ],
                    "operation": "delete file",
                    "file_fanin_percentile": graph["scores"]["symbol_scope_evidence"][
                        "file_fanin_rollup"
                    ]["percentile"],
                    "caller_count": graph["scores"]["symbol_scope_evidence"]["file_fanin_rollup"][
                        "distinct_caller_count"
                    ],
                    "risk_event": "dependency_break",
                    "risk_boost": g_dep - n_dep,
                    "final_probability": g_dep,
                },
            )
        ],
        out_dir=Path("e2e/out/reports"),
        run_id="external_graph_delta",
    )
    assert report.exists()
