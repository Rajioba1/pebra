"""Graph node styling: larger bold labels and an accessible file-vs-symbol two-colour scheme
(blue files / red symbols) that stays legible on the dark graph canvas. Source guards over app.js."""

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


def test_node_labels_are_larger_and_bold() -> None:
    base = _base_node_style(_js())
    size = re.search(r'"font-size":\s*(\d+)', base)
    assert size and int(size.group(1)) >= 12, "base node label must be >=12px"
    assert '"font-weight": "bold"' in base, "node labels must be bold"


def test_nodes_coloured_by_file_vs_symbol() -> None:
    js = _js()
    assert 'const FILE_COLOR = "#58a6ff"' in js, "accessible file (blue) colour token missing"
    assert 'const SYMBOL_COLOR = "#ff6b6b"' in js, "accessible symbol (red) colour token missing"
    assert '"background-color": SYMBOL_COLOR' in _base_node_style(js), "symbols default to red"
    assert "node[?is_file]" in js, "files need a dedicated selector"
    file_rule = re.search(r'"node\[\?is_file\]".*?FILE_COLOR', js, re.DOTALL)
    assert file_rule, "the is_file selector must paint files with FILE_COLOR"
    assert re.search(r"is_file:", js), "renderCy must tag nodes with is_file"
    assert 'swatchLabel(FILE_COLOR, "file")' in js, "legend must show the file colour"
    assert 'swatchLabel(SYMBOL_COLOR, "symbol")' in js, "legend must show the symbol colour"


def test_node_palette_is_legible_on_the_dark_canvas() -> None:
    for colour in ("#58a6ff", "#ff6b6b"):
        ratio = _contrast(colour, _GRAPH_BG)
        assert ratio >= _UI_CONTRAST, f"{colour} on {_GRAPH_BG} is {ratio:.2f}:1, below {_UI_CONTRAST}:1"
