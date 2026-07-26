"""Milestone 2: the CoSE layout config is defined once (no duplication between the initial-load and
button paths) and its edge length scales with node count instead of the flat 60 that no-ops at >=100
nodes. Structural guards over the served app.js; occupancy itself is proven in the ui-e2e lane."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"


def _js() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def test_cose_options_defined_once() -> None:
    js = _js()
    assert "function coseOptions(" in js, "expected a single shared coseOptions() helper"
    # The two consumers (initial layoutFor + button runGraphLayout) must both call the shared helper;
    # `name: "cose"` should appear exactly once — inside the helper, not inline at either call site.
    assert js.count('name: "cose"') == 1, "cose options must live only inside coseOptions()"
    assert js.count("coseOptions(") >= 3, "coseOptions must be defined and called from both paths"


def test_cose_edge_length_scales_with_node_count() -> None:
    js = _js()
    assert "idealEdgeLength: 60" not in js, "flat idealEdgeLength:60 no-ops at >=100 nodes"
    body = re.search(r"function coseOptions\([^)]*\)\s*\{(.*?)\n  \}", js, re.DOTALL)
    assert body, "coseOptions body not found"
    inner = body.group(1)
    assert re.search(r"const n = Math\.max\(1,\s*nodeCount\)", inner), "node count needs a divide-by-zero guard"
    # Both tunables must be a FUNCTION of the guarded count (divide by `n`), not a fresh constant that
    # merely happens to differ from 60 — otherwise the "scales with node count" guarantee is hollow.
    assert re.search(r"idealEdgeLength:[^\n]*/\s*n\b", inner), "idealEdgeLength must scale with node count"
    assert re.search(r"componentSpacing:[^\n]*/\s*n\b", inner), "componentSpacing must scale with node count"


def test_cose_spacing_is_bounded_for_tiny_graphs() -> None:
    js = _js()
    body = re.search(r"function coseOptions\([^)]*\)\s*\{(.*?)\n  \}", js, re.DOTALL)
    assert body, "coseOptions body not found"
    inner = body.group(1)
    assert re.search(r"idealEdgeLength:[^\n]*Math\.min\(", inner), (
        "idealEdgeLength needs an upper bound for tiny graphs"
    )
    assert re.search(r"componentSpacing:[^\n]*Math\.min\(", inner), (
        "componentSpacing needs an upper bound for tiny disconnected graphs"
    )
