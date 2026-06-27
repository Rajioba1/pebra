"""Phase-4 reframe (M5-prep) — pure structural feature schema v2.

Two honest fan-in signals: CONTAINER-FILE level (``container_file_fan_in_percentile``, import graph)
AND real per-symbol call-graph fan-in (``symbol_fan_in_percentile``, from the graph engine; 0.0 = no
trusted value, with trust context in provenance). M5c.5 added the per-symbol field; schema is v2.
"""

from __future__ import annotations

from pebra.core import structural_features as sf


def _build(**over):
    base = dict(
        symbol_id="src/payments/charge.py::calculateCharge",
        file_path="src/payments/charge.py",
        action_type="edit",
        change_kind="BEHAVIORAL",
        visibility="public_api",
        is_public_api=True,
        body_changed=True,
        signature_changed=False,
        container_file_fan_in_percentile=0.95,
        bridge_centrality=0.0,
        cycle_participation=False,
        is_architecture_anchor=False,
        domain_entrypoint=False,
        fan_out=3,
        dependency_boundary=False,
        matched_domains=["payments"],
        domain_criticality_hint=None,
        criticality_stage="C3",
        symbol_fan_in_percentile=0.0,
        consequential_symbol_changed=False,
        provenance={"structural_source": "structural_feature_adapter", "graph_freshness": "rebuilt"},
    )
    base.update(over)
    return sf.build_structural_features(**base)


def test_schema_version_and_blocks() -> None:
    f = _build()
    assert f["schema_version"] == sf.SCHEMA_VERSION == 2  # v2: real per-symbol fan-in added
    assert set(f) >= {"schema_version", "symbol", "structural", "domain", "provenance"}
    assert f["symbol"]["symbol_id"] == "src/payments/charge.py::calculateCharge"
    assert f["symbol"]["is_public_api"] is True
    assert f["domain"]["matched_domains"] == ["payments"]


def test_symbol_fan_in_present_alongside_container_v2() -> None:
    # v2: real per-symbol fan-in (from the graph engine) is carried, DISTINCT from container/file-level.
    f = _build(container_file_fan_in_percentile=0.95, symbol_fan_in_percentile=0.97)
    assert f["structural"]["container_file_fan_in_percentile"] == 0.95
    assert f["structural"]["is_high_container_fan_in"] is True
    assert f["structural"]["symbol_fan_in_percentile"] == 0.97
    assert f["structural"]["is_high_symbol_fan_in"] is True


def test_high_symbol_fan_in_threshold() -> None:
    assert _build(symbol_fan_in_percentile=0.89)["structural"]["is_high_symbol_fan_in"] is False
    assert _build(symbol_fan_in_percentile=0.90)["structural"]["is_high_symbol_fan_in"] is True


def test_consequential_symbol_changed_carried() -> None:
    assert _build(consequential_symbol_changed=True)["symbol"]["consequential_symbol_changed"] is True
    assert _build()["symbol"]["consequential_symbol_changed"] is False


def test_high_container_fan_in_threshold() -> None:
    assert _build(container_file_fan_in_percentile=0.89)["structural"]["is_high_container_fan_in"] is False
    assert _build(container_file_fan_in_percentile=0.90)["structural"]["is_high_container_fan_in"] is True


def test_provenance_carried() -> None:
    f = _build()
    assert f["provenance"]["graph_freshness"] == "rebuilt"
    assert f["provenance"]["structural_source"] == "structural_feature_adapter"
