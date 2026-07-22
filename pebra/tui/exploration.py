"""App-owned coordination for explicit, command-scoped repository exploration."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import CancelledError
from typing import Any, TypeAlias

from textual.app import App

from pebra.core.exploration import ExplorationResult
from pebra.core.graph_snapshot import GraphSnapshot, graph_snapshot_matches
from pebra.core.learning_context import LearningContextRecall
from pebra.ports.repository_explorer_port import (
    RepositoryExplorer,
    RepositoryExplorerFactory,
)

KnowledgeExplorer: TypeAlias = Callable[
    [str, str, tuple[str, ...], RepositoryExplorer],
    tuple[LearningContextRecall, ExplorationResult],
]


class RepositoryExplorationCoordinator:
    """Own one app-wide flight while creating a fresh provider session per command."""

    def __init__(
        self,
        factory: RepositoryExplorerFactory | None,
        *,
        knowledge_explorer: KnowledgeExplorer | None = None,
    ) -> None:
        self._factory = factory
        self._knowledge_explorer = knowledge_explorer
        self._lock = threading.Lock()
        self._busy = False
        self._shutting_down = False
        self._active: RepositoryExplorer | None = None
        self._generation = 0
        self._provider_done = threading.Event()
        self._provider_done.set()

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
        on_result: Callable[..., None],
        on_error: Callable[[], None],
    ) -> bool:
        with self._lock:
            if self._factory is None or self._busy or self._shutting_down:
                return False
            self._busy = True
            self._generation += 1
            generation = self._generation
            self._provider_done.clear()
        app.run_worker(
            lambda: self._run(
                app,
                generation=generation,
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
        generation: int,
        repo_root: str,
        query: str,
        files: tuple[str, ...],
        on_result: Callable[..., None],
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
            if self._knowledge_explorer is not None:
                recall, result = self._knowledge_explorer(
                    repo_root, query, files, explorer
                )
                callback_args = (result, recall)
            else:
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
                if not self._snapshot_matches(snapshot, result):
                    raise RuntimeError("provider returned a mismatched graph snapshot")
                callback_args = (result,)
            callback = on_result
        except Exception:  # provider/runtime boundary: the TUI remains read-only and usable
            callback = on_error
            callback_args = ()
        finally:
            with self._lock:
                self._active = None
                shutting_down = self._shutting_down
                self._provider_done.set()
        if shutting_down:
            return
        try:
            app.call_from_thread(
                self._deliver_if_current,
                generation,
                callback,
                callback_args,
            )
        except (CancelledError, RuntimeError):
            # App shutdown or a removed screen can invalidate the delivery target after completion.
            self._release_if_current(generation)
            return

    @staticmethod
    def _snapshot_matches(prepared: GraphSnapshot, result: ExplorationResult) -> bool:
        return graph_snapshot_matches(
            prepared,
            result.snapshot,
            result_available=result.status == "available",
        )

    def _deliver_if_current(
        self,
        generation: int,
        callback: Callable[..., None],
        callback_args: tuple[object, ...],
    ) -> None:
        """Deliver on the UI thread only while this operation is still the newest accepted one."""
        with self._lock:
            if self._shutting_down or generation != self._generation:
                return
        try:
            callback(*callback_args)
        finally:
            self._release_if_current(generation)

    def _release_if_current(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._busy = False

    def cancel(self, *, wait: bool = False) -> None:
        """Prevent new work and cooperatively stop the active provider process tree."""
        with self._lock:
            self._shutting_down = True
            self._generation += 1
            self._busy = False
            explorer = self._active
        if explorer is not None:
            explorer.cancel()
        if wait:
            self._provider_done.wait(timeout=1.5)
