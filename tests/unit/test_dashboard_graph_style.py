"""Graph node styling: progressive labels and a structural file-vs-symbol palette that remains
distinct from PEBRA's decision/benefit signal colours. Source guards over app.js."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"
_GRAPH_BG = "#12191f"  # radial-gradient centre of .graph-cy (style.css)
_UI_CONTRAST = 3.0  # WCAG minimum for non-text UI elements


def _js() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def _base_node_style(js: str) -> str:
    block = re.search(r'\{\s*selector:\s*"node",\s*style:\s*\{([^}]*)\}', js)
    assert block, "base node style block not found in cyStyle"
    return block.group(1)


def _relative_luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_symbol_labels_use_zoom_disclosure_while_hubs_stay_labelled() -> None:
    js = _js()
    base = _base_node_style(_js())
    size = re.search(r'"font-size":\s*(\d+)', base)
    assert size and int(size.group(1)) >= 11, "revealed symbol labels must be legible"
    assert '"font-weight": "bold"' not in base, "all symbol labels must not be bold at once"
    assert "SYMBOL_LABEL_ZOOM" in js, "symbol labels need a named zoom threshold"
    assert "updateSymbolLabelVisibility" in js, "symbol labels need threshold-crossing disclosure"
    assert "show-symbol-label" in js, "zoom disclosure needs a dedicated Cytoscape class"
    assert re.search(
        r'node\[graph_role="hub"\].*?"label":\s*"data\(label\)"',
        js,
        re.DOTALL,
    ), "god-map hubs must remain labelled at fitted zoom"


def test_nodes_coloured_by_file_vs_symbol() -> None:
    js = _js()
    assert 'const FILE_COLOR = "#8b949e"' in js, "neutral file colour token missing"
    assert 'const SYMBOL_COLOR = "#a78bfa"' in js, "structural symbol colour token missing"
    assert '"background-color": SYMBOL_COLOR' in _base_node_style(js), "symbols need the structural colour"
    assert "node[?is_file]" in js, "files need a dedicated selector"
    file_rule = re.search(r'"node\[\?is_file\]".*?FILE_COLOR', js, re.DOTALL)
    assert file_rule, "the is_file selector must paint files with FILE_COLOR"
    assert re.search(r"is_file:", js), "renderCy must tag nodes with is_file"
    assert 'swatchLabel(FILE_COLOR, "file")' in js, "legend must show the file colour"
    assert 'swatchLabel(SYMBOL_COLOR, "symbol")' in js, "legend must show the symbol colour"


def test_node_palette_is_legible_on_the_dark_canvas() -> None:
    for colour in ("#8b949e", "#a78bfa"):
        ratio = _contrast(colour, _GRAPH_BG)
        assert ratio >= _UI_CONTRAST, f"{colour} on {_GRAPH_BG} is {ratio:.2f}:1, below {_UI_CONTRAST}:1"


def test_structural_palette_does_not_reuse_signal_colours() -> None:
    js = _js()
    structural = {
        re.search(rf'const {name} = "(#[0-9a-f]+)"', js).group(1)
        for name in ("FILE_COLOR", "SYMBOL_COLOR")
    }
    signal = {"#3fb950", "#d6a419", "#f0883e", "#f85149", "#58a6ff"}
    assert structural.isdisjoint(signal), "structural node colours must not masquerade as risk or benefit"
