"""Phase E1 (unit): the human-review markdown report rendering (pure string build, no IO)."""

from __future__ import annotations

from e2e.utils import report_generator as rg


def test_render_includes_each_feature_status_and_run_id():
    results = [
        rg.FeatureResult("agent_risky_edit", "PASS", "agent-cli", notes="decision=inspect_first"),
        rg.FeatureResult("dashboard_visual", "NEEDS-HUMAN-REVIEW", "dashboard",
                         screenshot_path="out/screenshots/dash.png"),
    ]
    md = rg.render_report(results, run_id="run_x")
    assert "run_x" in md
    assert "agent_risky_edit" in md and "PASS" in md
    assert "dashboard_visual" in md and "NEEDS-HUMAN-REVIEW" in md
    assert "dash.png" in md  # screenshot is linked for the human


def test_render_includes_human_graph_and_learning_labels():
    md = rg.render_report(
        [
            rg.FeatureResult(
                "external_graph_delete",
                "PASS",
                "codegraph",
                graph_evidence={
                    "engine": "CodeGraph",
                    "freshness": "fresh",
                    "operation": "delete file",
                    "file_fanin_percentile": 1.0,
                    "caller_count": 13,
                    "risk_event": "dependency_break",
                    "risk_boost": 0.25,
                    "final_probability": 0.45,
                },
                learning_evidence={
                    "prior_success": 0.70,
                    "learned_success": 0.85,
                    "before_decision": "proceed",
                    "after_decision": "inspect_first",
                    "promotion_n": 105,
                    "real_build_cycles": 1,
                    "seeded_cycles": 104,
                },
            )
        ],
        run_id="r",
    )

    assert "Graph engine: CodeGraph" in md
    assert "Graph freshness: fresh" in md
    assert "Changed operation: delete file" in md
    assert "File fan-in rollup: 1.000 percentile" in md
    assert "Graph callers/references: 13" in md
    assert "Risk event added: dependency_break" in md
    assert "Graph risk boost: +0.250 p_event" in md
    assert "Final dependency-break probability: 0.450" in md
    assert "Prior success estimate: 0.700" in md
    assert "Learned success estimate: 0.850" in md
    assert "Decision before learning: proceed" in md
    assert "Decision after learning: inspect_first" in md
    assert "Promotion evidence: n=105 completed outcomes" in md
    assert "Real build outcomes: 1" in md
    assert "Seeded outcomes: 104" in md


def test_overall_is_fail_if_any_fail():
    md = rg.render_report([rg.FeatureResult("a", "PASS", "x"), rg.FeatureResult("b", "FAIL", "x")],
                          run_id="r")
    assert "OVERALL: FAIL" in md


def test_overall_is_needs_review_if_any_review_and_no_fail():
    md = rg.render_report(
        [rg.FeatureResult("a", "PASS", "x"), rg.FeatureResult("b", "NEEDS-HUMAN-REVIEW", "x")],
        run_id="r",
    )
    assert "OVERALL: NEEDS-HUMAN-REVIEW" in md


def test_overall_is_pass_when_all_pass():
    md = rg.render_report([rg.FeatureResult("a", "PASS", "x")], run_id="r")
    assert "OVERALL: PASS" in md


def test_invalid_status_is_rejected():
    try:
        rg.FeatureResult("a", "PAS", "x")
    except ValueError as exc:
        assert "status" in str(exc)
    else:
        raise AssertionError("invalid status should fail closed")
