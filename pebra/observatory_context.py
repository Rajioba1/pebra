"""Shared Observatory launch context (Observatory TUI M1).

The `pebra dashboard` CLI and the future `pebra tui` CLI both need the same db/identity resolution, and
the same load-bearing guarantee: `--read-only` supplies the identity directly and never resolves a repo,
so NO `.pebra/` is initialized anywhere. Centralizing it here means the two surfaces cannot drift on that
guarantee. This is composition/surface support — it may wire an adapter (the repository registry), and the
import contracts forbid core/app/adapters from importing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pebra.adapters.repository_registry import RepositoryRegistry


OBSERVATORY_LABEL_ENV = "PEBRA_OBSERVATORY_LABEL"


def observatory_display_label() -> str | None:
    """Return a bounded optional launch label used by isolated developer viewers."""
    import os

    raw = os.environ.get(OBSERVATORY_LABEL_ENV, "").strip()
    if not raw or any(ord(character) < 32 for character in raw):
        return None
    return raw[:32]


class ObservatoryContextError(ValueError):
    """The Observatory launch arguments are invalid: a bad `--read-only` combination, or a missing db."""


@dataclass(frozen=True)
class ObservatoryContext:
    db_path: str
    repo_id: str
    repo_root: str | None
    read_only: bool


def resolve_observatory_context(
    *, read_only: bool, db: str | None, repo_id: str | None, repo_root: str | None
) -> ObservatoryContext:
    """Resolve the db + identity a read-only Observatory surface (dashboard or TUI) will serve.

    read-only: identity is supplied, never resolved, so no `.pebra/` is initialized; requires `--db`
    (an existing file) and `--repo-id`. `--repo-root`, if given, is graph context only. normal: resolve
    the repo (uses/creates `.pebra/`) and default the db to ``<repo_root>/.pebra/pebra.db``.
    """
    if read_only:
        if not db or not repo_id:
            raise ObservatoryContextError("--read-only requires --db and --repo-id")
        if not Path(db).is_file():
            raise ObservatoryContextError(f"--read-only db does not exist: {db}")
        return ObservatoryContext(db_path=db, repo_id=repo_id, repo_root=repo_root, read_only=True)
    repo = RepositoryRegistry().resolve(repo_root or ".")
    return ObservatoryContext(
        db_path=db or str(Path(repo.repo_root) / ".pebra" / "pebra.db"),
        repo_id=repo_id or repo.repo_id,
        repo_root=repo.repo_root,
        read_only=False,
    )
