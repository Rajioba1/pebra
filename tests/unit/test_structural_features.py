"""Phase-4 reframe (M5-prep) — pure structural feature schema v1.

Honest naming: the v1 fan-in signal is CONTAINER-FILE level (the import graph has no per-symbol call
graph yet), so there is NO `symbol_fan_in_percentile` field — true per-symbol fan-in is a later
precision slice, not a fake zero.
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
        provenance={"structural_source": "structural_feature_adapter", "graph_freshness": "rebuilt"},
    )
    base.update(over)
    return sf.build_structural_features(**base)


def test_schema_version_and_blocks() -> None:
    f = _build()
    assert f["schema_version"] == sf.SCHEMA_VERSION == 1
    assert set(f) >= {"schema_version", "symbol", "structural", "domain", "provenance"}
    assert f["symbol"]["symbol_id"] == "src/payments/charge.py::calculateCharge"
    assert f["symbol"]["is_public_api"] is True
    assert f["domain"]["matched_domains"] == ["payments"]


def test_container_fan_in_named_honestly_no_symbol_fan_in() -> None:
    f = _build(container_file_fan_in_percentile=0.95)
    assert f["structural"]["container_file_fan_in_percentile"] == 0.95
    assert f["structural"]["is_high_container_fan_in"] is True  # >= ANCHOR_FANIN_PERCENTILE (0.90)
    # the honesty contract: no fake per-symbol fan-in anywhere in the payload
    assert "symbol_fan_in_percentile" not in f["structural"]
    assert "symbol_fan_in_percentile" not in f["symbol"]


def test_high_container_fan_in_threshold() -> None:
    assert _build(container_file_fan_in_percentile=0.89)["structural"]["is_high_container_fan_in"] is False
    assert _build(container_file_fan_in_percentile=0.90)["structural"]["is_high_container_fan_in"] is True


def test_provenance_carried() -> None:
    f = _build()
    assert f["provenance"]["graph_freshness"] == "rebuilt"
    assert f["provenance"]["structural_source"] == "structural_feature_adapter"
