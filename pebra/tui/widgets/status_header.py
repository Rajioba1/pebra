"""StatusHeader — repo, latest assessed commit, store-chain integrity, and count.

Chain integrity is labelled "store chain" because it is database-global (not repo-scoped). CodeGraph
health is deliberately NOT shown here — it is not a store read and must not be polled on this surface.
"""

from __future__ import annotations

from textual.widgets import Static

from pebra.tui.widgets.ledger_table import short_commit


def _short_repo(repo_id: str) -> str:
    # A stable compact slug so the status line fits a ~70-column pane: drop a "repo_" prefix and keep
    # the leading hash. Display-only — the full repo_id is the bound identity, not this.
    slug = repo_id[len("repo_") :] if repo_id.startswith("repo_") else repo_id
    return slug[:8] or repo_id


def format_status(*, repo_id: str, latest_commit: str | None, chain_valid: bool, total: int) -> str:
    # Permanently compact so it never wraps on a normal terminal (nowrap+ellipsis in TCSS is only a
    # last-resort safety net). The commit comes from persisted assessment history, never live Git.
    assessed = short_commit(latest_commit)
    chain = "ok" if chain_valid else "BROKEN"
    return (
        f"repo {_short_repo(repo_id)} · latest assessed {assessed} · "
        f"store chain {chain} · {total} asm"
    )


class StatusHeader(Static):
    def update_status(
        self, *, repo_id: str, latest_commit: str | None, chain_valid: bool, total: int
    ) -> None:
        self.status_text = format_status(
            repo_id=repo_id, latest_commit=latest_commit, chain_valid=chain_valid, total=total
        )
        self.update(self.status_text)
