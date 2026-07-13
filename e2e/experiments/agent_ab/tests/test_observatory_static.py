from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).resolve().parents[1] / "runners" / "observatory" / "static"


def test_matrix_distinguishes_not_planned_from_pending():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert 'class: "cell na", title: "not planned"' in app_js
    assert 'class: "cell pending", title: "pending"' in app_js
    assert ".cell.na" in css
    assert "not planned" in app_js


def test_data_tables_use_fixed_layout_for_stable_columns():
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    assert "table.data { width: 100%; table-layout: fixed;" in css


def test_numeric_headers_align_with_numeric_cells():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert 'el("th", { class: "num", text: "n" })' in app_js
    assert 'el("th", { class: "num", text: "harm" })' in app_js
    assert "table.data th.num, table.data td.num" in css


def test_no_attempt_matrix_state_is_visible_before_over_caution():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert app_js.index("if (s.no_attempt)") < app_js.index("else if (s.over_cautious)")
    assert 'class: "cell noattempt"' in app_js
    assert ".cell.noattempt" in css


def test_trace_panel_is_rendered_with_dom_apis():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert "function renderTraces" in app_js
    assert "subject_trace.json sidecars" in app_js
    assert "renderTraces(v.traces)" in app_js
    assert ".trace-wrap" in css
    assert ".trace-timeout" in css
    assert ".innerHTML" not in app_js


def test_run_failure_kinds_are_visible_in_the_observatory():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert "v.phase_detail.error" in app_js
    assert ".pill.insufficient_data" in css
    assert ".pill.no_headroom" in css


def test_graph_refined_completion_is_visible_in_scoreboard_and_matrix():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")

    assert "graph-authorized + post-verify" in app_js
    assert "graph + pre-edit host verification + post-verify" in app_js
    assert "graph_only_autonomous_completion_rate" in app_js
    assert "graph_plus_host_verified_completion_rate" in app_js
    assert "graph_only_autonomous_completion_gain" in app_js
    assert "graph_plus_host_verified_completion_gain" in app_js
    assert "graph_refinement_proof_path" in app_js
    assert "post_edit_verify_assessment_id" in app_js
    assert "graph_refinement_origin_benefit" in app_js
    assert "graph_refinement_revised_benefit" in app_js
    assert "graph_refinement_origin_expected_utility" in app_js
    assert "graph_refinement_revised_expected_utility" in app_js
    assert "measured_benefit" in app_js
