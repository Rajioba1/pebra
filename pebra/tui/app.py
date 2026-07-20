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
from pebra.provenance import provenance_line
from pebra.ports.repository_explorer_port import RepositoryExplorer
from pebra.tui.data import ObservatoryData
from pebra.tui.screens.observatory import ObservatoryScreen
from pebra.tui.theme import css_variables

_CSS_PATH = Path(__file__).parent / "theme.tcss"


class ObservatoryApp(App[None]):
    CSS_PATH = _CSS_PATH
    TITLE = "PEBRA Observatory"
    # Width/height breakpoint classes on the screen (theme.tcss uses them to keep the banner off
    # short/narrow terminals and show its tagline only when there's room).
    HORIZONTAL_BREAKPOINTS = [(0, "-cramped"), (80, "-normal"), (100, "-wide")]
    VERTICAL_BREAKPOINTS = [(0, "-short"), (28, "-tall")]
    # `q` is our convenience quit. `ctrl+q` is inherited from Textual's base App (priority=True,
    # hidden) — do NOT redeclare it here: the tuple form would drop its priority, letting a focused
    # widget (the ledger DataTable) intercept ctrl+q before the app-level quit.
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("?", "show_help_panel", "pebra --help"),
        ("escape", "close_help_panel", "Back"),
    ]

    def __init__(
        self, context: ObservatoryContext, *, explorer: RepositoryExplorer | None = None
    ) -> None:
        super().__init__()
        # NOTE: not `self._context` — that name is a Textual App internal (the app-context manager).
        self.observatory_context = context
        self.repository_explorer = explorer
        # Source provenance in the header subtitle, so you can tell the checkout from the released wheel.
        # Computed once here (may shell out to git for an editable install) — never on the 5s refresh.
        self.sub_title = provenance_line(prefix=False)

    def get_default_screen(self) -> Screen:
        return ObservatoryScreen(
            ObservatoryData(self.observatory_context),
            repo_root=self.observatory_context.repo_root,
            explorer=self.repository_explorer,
        )

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(css_variables())
        return variables

    def action_show_help_panel(self) -> None:
        """Toggle Textual's key-binding help panel."""
        from textual.widgets import HelpPanel

        panels = self.screen.query(HelpPanel)
        if len(panels):
            self.action_close_help_panel()
        else:
            self.screen.mount(HelpPanel())
            self.call_after_refresh(self.refresh_bindings)

    def action_close_help_panel(self) -> None:
        """Close the key-binding help panel when it is open."""
        from textual.widgets import HelpPanel

        self.screen.query(HelpPanel).remove()
        self.call_after_refresh(self.refresh_bindings)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "close_help_panel":
            from textual.widgets import HelpPanel

            return len(self.screen.query(HelpPanel)) > 0
        return super().check_action(action, parameters)

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        # Built-ins (Change theme, Quit, ...) plus the Observatory's read-only commands. There is
        # deliberately NO mutating command here — this surface only reads.
        yield from super().get_system_commands(screen)
        if isinstance(screen, ObservatoryScreen):
            yield SystemCommand(
                "Refresh", "Reload the ledger from the store now", self._command_refresh
            )
            yield SystemCommand("Overview", "Show decision/status counts", self._command_overview)
            grouping_title = "Show raw" if screen.group_repeats else "Group repeats"
            yield SystemCommand(
                grouping_title,
                "Toggle contiguous exact-candidate grouping",
                screen.action_toggle_grouping,
            )
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


def run_observatory(
    context: ObservatoryContext, *, explorer: RepositoryExplorer | None = None
) -> None:
    """Run the Observatory with an optional injected descriptive explorer."""
    ObservatoryApp(context, explorer=explorer).run()
