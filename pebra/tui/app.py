"""ObservatoryApp — the Textual shell for the read-only PEBRA Observatory.

M2 is the shell only: Header + Footer, the packaged TCSS theme, merged Observatory theme variables, and
q / Ctrl+Q to quit. It carries the resolved ObservatoryContext but does NOT read the store yet — the
status header, ledger, RAU-lane, and detail screens (and all data wiring) arrive in later milestones.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from pebra.observatory_context import ObservatoryContext
from pebra.tui.theme import css_variables

_CSS_PATH = Path(__file__).parent / "theme.tcss"


class ObservatoryApp(App[None]):
    CSS_PATH = _CSS_PATH
    TITLE = "PEBRA Observatory"
    # `q` is our convenience quit. `ctrl+q` is inherited from Textual's base App (priority=True,
    # hidden) — do NOT redeclare it here: the tuple form would drop its priority, letting a focused
    # widget (e.g. the ledger DataTable added later) intercept ctrl+q before the app-level quit.
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, context: ObservatoryContext) -> None:
        super().__init__()
        # NOTE: not `self._context` — that name is a Textual App internal (the app-context manager);
        # shadowing it breaks run()/run_test(). Later milestones read the store through this.
        self.observatory_context = context

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(css_variables())
        return variables


def run_observatory(context: ObservatoryContext) -> None:
    """Blocking entry point used by `pebra tui` — construct and run the app for the resolved context."""
    ObservatoryApp(context).run()
