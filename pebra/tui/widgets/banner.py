"""PebraBanner — the Observatory's compact wordmark.

Not a splash logo: it's a tiny instance of the instrument itself. The name sits in considered letter-
spacing beside the product's signature — the RAU lane with the gate (│) and a proceed marker (▸) — so the
mark literally reads as "the gate an edit passes through, hoping to proceed." Two lines at most; it is
hidden on short/narrow terminals (breakpoint classes in theme.tcss) so it never steals space from the
ledger. The one-time reveal (the marker sweeping to the gate) is added on top of this static form.
"""

from __future__ import annotations

from textual.content import Content
from textual.widgets import Static

_WORDMARK = "P E B R A   "
_TAGLINE = "pre-edit benefit / risk"
_LANE_LEN = 15
_GATE_INDEX = 7
_REST_INDEX = _LANE_LEN - 1  # the marker's settled position (far right of the lane)


def lane(marker_index: int) -> str:
    """The wordmark's lane: a `·` track with the gate `│` at center and the `▸` marker at marker_index
    (which overrides whatever cell it lands on). marker_index < 0 draws no marker."""
    cells = ["·"] * _LANE_LEN
    cells[_GATE_INDEX] = "│"
    if 0 <= marker_index < _LANE_LEN:
        cells[marker_index] = "▸"
    return "".join(cells)


def banner_content(marker_index: int) -> Content:
    """Two lines: the spaced wordmark + lane, then the muted tagline."""
    line1 = _WORDMARK + lane(marker_index)
    return Content.assemble(f"{line1}\n", (_TAGLINE, "dim"))


_REVEAL_STEP = 0.04  # seconds per frame; one pass over the lane is ~0.6s


class PebraBanner(Static):
    _reveal_timer = None
    _marker = _REST_INDEX

    def on_mount(self) -> None:
        # Reduced motion (TEXTUAL_ANIMATIONS=none): show the settled mark at once, no sweep.
        if self.app.animation_level == "none":
            self.settle()
            return
        # One-time reveal: the proceed marker sweeps from the left, through the gate, to its rest —
        # once, on mount only. It is never restarted (the 5s refresh does not touch the banner).
        self._marker = 0
        self.update(banner_content(self._marker))
        self._reveal_timer = self.set_interval(_REVEAL_STEP, self._advance_reveal)

    def _advance_reveal(self) -> None:
        self._marker += 1
        if self._marker >= _REST_INDEX:
            self.settle()  # one pass complete — static forever after
            return
        self.update(banner_content(self._marker))

    def settle(self) -> None:
        """Stop any in-flight reveal and show the final, static mark (marker at rest)."""
        if self._reveal_timer is not None:
            self._reveal_timer.stop()
            self._reveal_timer = None
        self._marker = _REST_INDEX
        self.update(banner_content(_REST_INDEX))
