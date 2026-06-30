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
