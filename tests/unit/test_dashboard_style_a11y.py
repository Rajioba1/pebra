"""Milestone 1 restyle a11y guards: text contrast, a 12px UI-label floor, and a non-colour-only
distribution bar. These parse the served static assets directly (no server needed)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_STYLE = ROOT / "pebra" / "dashboard" / "static" / "style.css"
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"

# Text selectors that render small copy on the --surface card background; all must meet WCAG AA.
_DIM_TEXT_SELECTORS = (".eyebrow", "thead th", ".control-label", ".empty", ".chart-note")
_SURFACE_TOKEN = "--surface"
_AA_CONTRAST = 4.5


def _root_tokens(css: str) -> dict[str, str]:
    block = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    assert block, ":root token block not found in style.css"
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", block.group(1)))


def _resolve(value: str, tokens: dict[str, str]) -> str:
    var = re.match(r"var\(\s*(--[\w-]+)\s*\)", value.strip())
    hex_value = tokens[var.group(1)] if var else value.strip()
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", hex_value), f"unresolved colour: {value!r}"
    return hex_value


def _relative_luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _rule_color(css: str, selector: str) -> str:
    block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css, re.DOTALL)
    assert block, f"selector {selector!r} not found in style.css"
    colour = re.search(r"(?<![-\w])color\s*:\s*([^;]+);", block.group(1))
    assert colour, f"selector {selector!r} declares no color"
    return colour.group(1)


def _decision_bar_source(js: str) -> str:
    start = js.index("function decisionBar(")
    return js[start : start + 1000]


def test_dim_text_selectors_meet_aa_contrast_on_surface() -> None:
    css = _STYLE.read_text(encoding="utf-8")
    tokens = _root_tokens(css)
    surface = tokens[_SURFACE_TOKEN]
    for selector in _DIM_TEXT_SELECTORS:
        colour = _resolve(_rule_color(css, selector), tokens)
        ratio = _contrast(colour, surface)
        assert ratio >= _AA_CONTRAST, (
            f"{selector} text ({colour}) on {surface} is {ratio:.2f}:1, below AA {_AA_CONTRAST}:1"
        )


def test_no_css_font_size_below_12px() -> None:
    css = _STYLE.read_text(encoding="utf-8")
    below = sorted(
        {int(n) for n in re.findall(r"font-size:\s*(\d+)px", css) if int(n) < 12}
    )
    assert not below, f"style.css has sub-12px UI text sizes: {below}"


def test_distribution_bar_is_not_colour_only() -> None:
    src = _decision_bar_source(_APP_JS.read_text(encoding="utf-8"))
    assert 'setAttribute("role", "img")' in src, "distbar wrapper needs role=img"
    assert "aria-label" in src, "distbar needs an aria-label summarising the distribution"
    assert 'setAttribute("aria-hidden", "true")' in src, "decorative segments must be aria-hidden"
    # The label must be BUILT from the data and guard total===0, not a static/NaN-prone string.
    assert "total ?" in src, "percentage math must guard total===0 to avoid NaN in the label"
    assert "summary.join" in src, "aria-label must be assembled from the per-decision summary"
