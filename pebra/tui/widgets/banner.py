"""PebraBanner — the Observatory's compact wordmark.

Not a splash logo: it's a tiny instance of the instrument itself. The name sits in considered letter-
spacing beside the product's signature — the RAU lane with its gate (│). A neutral ASCII pulse sweeps
across the lane once, then disappears; it deliberately never uses a decision glyph or represents stored
assessment state. Two lines at most; it is hidden on short/narrow terminals (breakpoint classes in
theme.tcss) so it never steals space from the ledger.
"""

from __future__ import annotations

from textual.content import Content
from textual.widgets import Static

_WORDMARK = "P E B R A   "
_TAGLINE = "pre-edit benefit / risk"
_LANE_LEN = 15
_GATE_INDEX = 7
_PULSE = "*"                 # decorative reveal only — never one of the verdict glyphs
_REST_INDEX = -1             # settled banner has no marker, so it cannot imply assessment state


def lane(marker_index: int) -> str:
    """The wordmark's lane: a `·` track with the gate `│` at center and an optional neutral pulse.

    The pulse overrides the track cell it lands on during the one-time reveal. A negative index draws
    the settled, marker-free lane.
    """
    cells = ["·"] * _LANE_LEN
    cells[_GATE_INDEX] = "│"
    if 0 <= marker_index < _LANE_LEN:
        cells[marker_index] = _PULSE
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
        # One-time reveal: a neutral pulse sweeps across the lane and disappears. It is never
        # restarted (the 5s refresh does not touch the banner).
        self._marker = 0
        self.update(banner_content(self._marker))
        self._reveal_timer = self.set_interval(_REVEAL_STEP, self._advance_reveal)

    def _advance_reveal(self) -> None:
        self._marker += 1
        if self._marker >= _LANE_LEN:
            self.settle()  # one pass complete — static forever after
            return
        self.update(banner_content(self._marker))

    def settle(self) -> None:
        """Stop any in-flight reveal and show the final, static, marker-free mark."""
        if self._reveal_timer is not None:
            self._reveal_timer.stop()
            self._reveal_timer = None
        self._marker = _REST_INDEX
        self.update(banner_content(_REST_INDEX))
