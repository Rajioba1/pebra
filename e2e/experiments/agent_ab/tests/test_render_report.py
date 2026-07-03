from __future__ import annotations

from e2e.experiments.agent_ab.models import ABMetrics, ArmMetrics
from e2e.experiments.agent_ab.reports import render_report


def _arm(
    arm, *, harm=0.0, over=0.0, completion=1.0, cycles=1.0,
    adherence=None, heeded=None, effective=None,
):
    return ArmMetrics(arm=arm, n_runs=3, n_risky=2, n_safe=1, harm_rate=harm,
                      over_caution_rate=over, quality_failure_rate=0.0,
                      task_completion_rate=completion,
                      mean_edit_cycles=cycles, adherence_rate=adherence, heeded_rate=heeded,
                      effective_adherence_rate=effective)


def _ab(*, harm_avoided, over_delta, adherence):
    net = harm_avoided - over_delta
    return ABMetrics(
        control=_arm("control", harm=0.6, over=0.0),
        treatment=_arm("treatment", harm=0.6 - harm_avoided, over=over_delta, adherence=adherence,
                       heeded=1.0, effective=adherence),
        harm_avoided_rate=harm_avoided, over_caution_delta=over_delta, net_benefit=net,
        n_pairs_risky=2, n_pairs_safe=1, cohens_d_paired=0.5, wilcoxon_w=1.0, wilcoxon_p=0.2,
        harm_diff_ci95=(0.1, 0.9),
    )


def test_to_json_records_served_models():
    m = _ab(harm_avoided=0.4, over_delta=0.0, adherence=0.9)
    j = render_report.to_json(m, served_models=["claude-haiku-4-5-20251001"])
    assert j["served_models"] == ["claude-haiku-4-5-20251001"]


def test_markdown_records_served_models():
    m = _ab(harm_avoided=0.4, over_delta=0.0, adherence=0.9)
    md = render_report.render_markdown(
        m, run_id="r", served_models=["claude-haiku-4-5-20251001"],
    )
    assert "claude-haiku-4-5-20251001" in md


def test_markdown_renders_all_endpoints():
    md = render_report.render_markdown(_ab(harm_avoided=0.4, over_delta=0.0, adherence=0.9),
                                       run_id="r")
    for label in ["harm_rate", "over_caution_rate", "quality_failure_rate", "task_completion_rate",
                  "mean_edit_cycles", "adherence_rate", "harm_avoided_rate", "net_benefit"]:
        assert label in md


def test_conclusion_tool_not_adopted_when_low_adherence():
    c = render_report.conclusion(_ab(harm_avoided=0.4, over_delta=0.0, adherence=0.2))
    assert "TOOL NOT ADOPTED" in c


def test_conclusion_adherence_boundary():
    # Floor is 0.33 (from config, single source). Just below -> non-informative; just above -> informative.
    below = render_report.conclusion(_ab(harm_avoided=0.4, over_delta=0.0, adherence=0.32))
    above = render_report.conclusion(_ab(harm_avoided=0.4, over_delta=0.0, adherence=0.34))
    assert "TOOL NOT ADOPTED" in below
    assert "TOOL NOT ADOPTED" not in above and "DIRECTIONAL" in above


def test_conclusion_uses_effective_adherence_not_mere_calls():
    m = ABMetrics(
        control=_arm("control", harm=0.6, over=0.0),
        treatment=_arm("treatment", harm=0.2, over=0.0, adherence=1.0, heeded=1.0, effective=0.0),
        harm_avoided_rate=0.4, over_caution_delta=0.0, net_benefit=0.4,
        n_pairs_risky=2, n_pairs_safe=1, cohens_d_paired=0.5, wilcoxon_w=1.0, wilcoxon_p=0.2,
        harm_diff_ci95=(0.1, 0.9),
    )
    assert "TOOL NOT ADOPTED" in render_report.conclusion(m)


def test_conclusion_no_net_benefit_when_nonpositive():
    c = render_report.conclusion(_ab(harm_avoided=0.1, over_delta=0.3, adherence=0.9))
    assert "NO NET BENEFIT" in c


def test_conclusion_directional_when_positive():
    c = render_report.conclusion(_ab(harm_avoided=0.4, over_delta=0.0, adherence=0.9))
    assert "DIRECTIONAL" in c and "no statistical" in c.lower()


def test_to_json_has_endpoint_block_and_conclusion():
    j = render_report.to_json(_ab(harm_avoided=0.4, over_delta=0.0, adherence=0.9))
    assert set(j["endpoints"]) >= {
        "harm_rate", "harm_avoided_rate", "quality_failure_rate", "net_benefit", "adherence_rate",
    }
    assert "conclusion" in j


def test_scoring_mode_is_self_describing():
    m = _ab(harm_avoided=0.4, over_delta=0.0, adherence=0.9)
    assert render_report.to_json(m, scoring_mode="build_test_scope")["scoring_mode"] == "build_test_scope"
    # default is the honest build-break label; markdown surfaces it too
    assert render_report.to_json(m)["scoring_mode"] == "build_break_scope"
    assert "build_test_scope" in render_report.render_markdown(m, run_id="r",
                                                               scoring_mode="build_test_scope")
