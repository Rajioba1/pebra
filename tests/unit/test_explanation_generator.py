"""Architecture §4/§8 — explanation_generator: pure semantic fields for the human card.

Produces bands + Why lines from the result; the surface composes layout. "RAU" is never a band
value; Value After Risk is a band (Negative/Borderline/Positive/Strong).
"""

from __future__ import annotations

from pebra.core import assessment_builder as ab
from pebra.core import decision_engine as de
from pebra.core import explanation_generator as eg
from tests.unit.test_assessment_builder import _worked_example_input


def _worked_result():
    return de.decide(ab.build_assessment(_worked_example_input()))


def test_band_helpers() -> None:
    assert eg.risk_level_band(0.10) == "Low"
    assert eg.risk_level_band(0.50) == "Moderate"
    assert eg.risk_level_band(0.80) == "High"
    assert eg.risk_level_band(1.2) == "Critical"
    bands = {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40}
    assert eg.value_after_risk_band(-0.1, bands) == "Negative"
    assert eg.value_after_risk_band(0.10, bands) == "Borderline"
    assert eg.value_after_risk_band(0.31, bands) == "Positive"
    assert eg.value_after_risk_band(0.50, bands) == "Strong"


def test_worked_example_card_fields() -> None:
    ex = eg.render(_worked_result())
    assert ex.risk_level_band == "Moderate"
    assert ex.value_after_risk_band == "Positive"
    assert ex.confidence_band == "high"
    assert ex.confidence_percent == 83
    assert ex.code_sensitivity_label == "High"
    assert ex.expected_damage == 0.10
    assert ex.risk_budget_percent == 50


def test_worked_example_why_lines_are_grounded_in_numbers() -> None:
    ex = eg.render(_worked_result())
    why = " ".join(ex.why)
    assert "50%" in why  # risk budget
    assert "0.10" in why or "0.1" in why  # expected loss
    assert "C3" in why  # criticality
    assert any("Value After Risk is Positive" in line for line in ex.why)
    # never leak the raw "RAU" acronym into human text
    assert all("RAU" not in line for line in ex.why)

def test_impact_witness_edge_site_why_line() -> None:
    lines = eg._impact_witness_why_lines({
        "symbol_fanin": {
            "resolution_method": "location",
            "graph_freshness": "fresh",
            "impact_witnesses": [
                {
                    "owner_qualified_name": "pkg.changed",
                    "dependent_qualified_name": "pkg.caller",
                    "file_path": "src/caller.py",
                    "line": 42,
                    "column": 7,
                    "edge_kind": "calls",
                    "depth": 1,
                    "location_source": "edge_site",
                }
            ],
        }
    })
    assert lines == [
        "Impact witness: pkg.caller in src/caller.py:42:7 calls changed symbol pkg.changed."
    ]


def test_impact_witness_transitive_definition_why_line() -> None:
    lines = eg._impact_witness_why_lines({
        "symbol_fanin": {
            "resolution_method": "location",
            "graph_freshness": "fresh",
            "impact_witnesses": [
                {
                    "owner_qualified_name": "pkg.changed",
                    "dependent_qualified_name": "pkg.indirect",
                    "file_path": "src/indirect.py",
                    "line": 18,
                    "column": None,
                    "edge_kind": "",
                    "depth": 2,
                    "location_source": "node_definition",
                }
            ],
        }
    })
    assert lines == [
        "Impact witness: pkg.indirect in src/indirect.py:18 is reachable from changed symbol "
        "pkg.changed at dependency depth 2 (dependent definition location, not a complete path)."
    ]
    assert all("complete path" in line or "reachable" in line for line in lines)
    assert "calls" not in lines[0]


def test_impact_witness_why_omitted_when_graph_untrusted() -> None:
    lines = eg._impact_witness_why_lines({
        "symbol_fanin": {
            "resolution_method": "unresolved",
            "graph_freshness": "stale",
            "impact_witnesses": [
                {
                    "owner_qualified_name": "pkg.changed",
                    "dependent_qualified_name": "pkg.caller",
                    "file_path": "src/caller.py",
                    "line": 1,
                    "column": 1,
                    "edge_kind": "calls",
                    "depth": 1,
                    "location_source": "edge_site",
                }
            ],
        }
    })
    assert lines == []


def test_impact_witness_why_capped_at_five() -> None:
    witnesses = [
        {
            "owner_qualified_name": "pkg.changed",
            "dependent_qualified_name": f"pkg.d{i}",
            "file_path": f"src/d{i}.py",
            "line": i,
            "column": 1,
            "edge_kind": "calls",
            "depth": 1,
            "location_source": "edge_site",
        }
        for i in range(1, 8)
    ]
    lines = eg._impact_witness_why_lines({
        "symbol_fanin": {
            "resolution_method": "location",
            "graph_freshness": "fresh",
            "impact_witnesses": witnesses,
        }
    })
    assert len(lines) == 5


def test_worked_example_why_unchanged_without_witnesses() -> None:
    ex = eg.render(_worked_result())
    assert all(not line.startswith("Impact witness:") for line in ex.why)
