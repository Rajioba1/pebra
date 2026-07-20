"""App-owned coordination for explicit, command-scoped repository exploration."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import CancelledError
from typing import Any

from textual.app import App

from pebra.core.exploration import ExplorationResult
from pebra.ports.repository_explorer_port import (
    RepositoryExplorer,
    RepositoryExplorerFactory,
)


class RepositoryExplorationCoordinator:
    """Own one app-wide flight while creating a fresh provider session per command."""

    def __init__(self, factory: RepositoryExplorerFactory | None) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._busy = False
        self._shutting_down = False
        self._active: RepositoryExplorer | None = None
        self._done = threading.Event()
        self._done.set()

    @property
    def available(self) -> bool:
        return self._factory is not None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def start(
        self,
        app: App[Any],
        *,
        repo_root: str,
        query: str,
        files: tuple[str, ...],
        on_result: Callable[[ExplorationResult], None],
        on_error: Callable[[], None],
    ) -> bool:
        with self._lock:
            if self._factory is None or self._busy or self._shutting_down:
                return False
            self._busy = True
            self._done.clear()
        app.run_worker(
            lambda: self._run(
                app,
                repo_root=repo_root,
                query=query,
                files=files,
                on_result=on_result,
                on_error=on_error,
            ),
            name="repository-exploration",
            group="repository-exploration",
            exit_on_error=False,
            thread=True,
        )
        return True

    def _run(
        self,
        app: App[Any],
        *,
        repo_root: str,
        query: str,
        files: tuple[str, ...],
        on_result: Callable[[ExplorationResult], None],
        on_error: Callable[[], None],
    ) -> None:
        callback: Callable[..., None] = on_error
        callback_args: tuple[object, ...] = ()
        explorer: RepositoryExplorer | None = None
        try:
            factory = self._factory
            if factory is None:
                raise RuntimeError("repository explorer unavailable")
            explorer = factory()
            with self._lock:
                self._active = explorer
                shutting_down = self._shutting_down
            if shutting_down:
                explorer.cancel()
                return
            snapshot = explorer.prepare(repo_root)
            with self._lock:
                shutting_down = self._shutting_down
            if shutting_down:
                explorer.cancel()
                return
            result = explorer.explore(
                repo_root,
                query,
                snapshot=snapshot,
                files=files,
            )
            if result.snapshot != snapshot:
                raise RuntimeError("provider returned a mismatched graph snapshot")
            callback = on_result
            callback_args = (result,)
        except Exception:  # provider/runtime boundary: the TUI remains read-only and usable
            callback = on_error
            callback_args = ()
        finally:
            with self._lock:
                self._active = None
                self._busy = False
                shutting_down = self._shutting_down
                self._done.set()
        if shutting_down:
            return
        try:
            app.call_from_thread(callback, *callback_args)
        except (CancelledError, RuntimeError):
            # App shutdown or a removed screen can invalidate the delivery target after completion.
            return

    def cancel(self, *, wait: bool = False) -> None:
        """Prevent new work and cooperatively stop the active provider process tree."""
        with self._lock:
            self._shutting_down = True
            explorer = self._active
        if explorer is not None:
            explorer.cancel()
        if wait:
            self._done.wait(timeout=1.5)
