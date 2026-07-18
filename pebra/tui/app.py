"""ObservatoryApp — the Textual shell for the read-only PEBRA Observatory.

The app owns the chrome (title, packaged TCSS theme, merged theme variables, q/Ctrl+Q quit) and mounts the
ObservatoryScreen as its default screen. It carries the resolved ObservatoryContext and hands it to the
screen via ObservatoryData; all store reads happen there, through the M1 shared query controller.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from textual.app import App, SystemCommand
from textual.screen import Screen

from pebra.observatory_context import ObservatoryContext
from pebra.tui.data import ObservatoryData
from pebra.tui.screens.observatory import ObservatoryScreen
from pebra.tui.theme import css_variables

_CSS_PATH = Path(__file__).parent / "theme.tcss"


class ObservatoryApp(App[None]):
    CSS_PATH = _CSS_PATH
    TITLE = "PEBRA Observatory"
    # `q` is our convenience quit. `ctrl+q` is inherited from Textual's base App (priority=True,
    # hidden) — do NOT redeclare it here: the tuple form would drop its priority, letting a focused
    # widget (the ledger DataTable) intercept ctrl+q before the app-level quit.
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, context: ObservatoryContext) -> None:
        super().__init__()
        # NOTE: not `self._context` — that name is a Textual App internal (the app-context manager).
        self.observatory_context = context

    def get_default_screen(self) -> Screen:
        return ObservatoryScreen(ObservatoryData(self.observatory_context))

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(css_variables())
        return variables

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        # Built-ins (Change theme, Quit, ...) plus the Observatory's read-only commands. There is
        # deliberately NO mutating command here — this surface only reads.
        yield from super().get_system_commands(screen)
        if isinstance(screen, ObservatoryScreen):
            yield SystemCommand(
                "Refresh", "Reload the ledger from the store now", self._command_refresh
            )
            yield SystemCommand("Overview", "Show decision/status counts", self._command_overview)
        yield SystemCommand("Help", "Show the key bindings", self.action_show_help_panel)

    def _command_refresh(self) -> None:
        screen = self.screen
        if isinstance(screen, ObservatoryScreen):
            if screen.action_refresh():
                self.notify("Refreshing the ledger…", timeout=3)  # transient success toast

    def _command_overview(self) -> None:
        screen = self.screen
        if isinstance(screen, ObservatoryScreen):
            self.notify(screen.overview_summary(), title="Overview", timeout=6)


def run_observatory(context: ObservatoryContext) -> None:
    """Blocking entry point used by `pebra tui` — construct and run the app for the resolved context."""
    ObservatoryApp(context).run()
