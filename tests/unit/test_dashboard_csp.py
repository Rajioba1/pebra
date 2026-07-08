"""CSP regression guard for the Risk Observatory front-end.

The dashboard ships a strict CSP (``style-src 'self'``, no ``'unsafe-inline'``): inline ``style="..."``
attributes, ``<style>`` blocks, and ``setAttribute('style', ...)`` are all blocked by the browser and
would silently break the page. Setting individual CSSOM properties (``el.style.width = ...``) is fine and
must NOT trip this guard. Vendored assets (``static/vendor/``) are third-party and out of scope.
"""

from __future__ import annotations

import re
from pathlib import Path

_DASH = Path(__file__).resolve().parents[2] / "pebra" / "dashboard"
_STYLE_ATTR = re.compile(r"""style\s*=\s*['"]""")
_SETATTR_STYLE = re.compile(r"""setAttribute\(\s*['"]style['"]""")
_STYLE_BLOCK = re.compile(r"<style", re.IGNORECASE)


def _authored_sources() -> list[Path]:
    files = list((_DASH / "templates").glob("*.html"))
    files += list((_DASH / "static").glob("*.js"))  # NOT recursive -> excludes static/vendor/
    return files


def test_no_inline_styles_anywhere() -> None:
    offenders: list[str] = []
    for path in _authored_sources():
        text = path.read_text(encoding="utf-8")
        if _STYLE_ATTR.search(text):
            offenders.append(f"{path.name}: inline style= attribute")
        if _SETATTR_STYLE.search(text):
            offenders.append(f"{path.name}: setAttribute('style', ...)")
        if path.suffix == ".html" and _STYLE_BLOCK.search(text):
            offenders.append(f"{path.name}: <style> block")
    assert offenders == [], f"CSP-forbidden inline styles found: {offenders}"


def test_guard_actually_scans_the_frontend() -> None:
    # Fail loudly if the glob ever stops finding the real files (a green vacuous pass would hide regressions).
    names = {p.name for p in _authored_sources()}
    assert "index.html" in names
    assert "app.js" in names
