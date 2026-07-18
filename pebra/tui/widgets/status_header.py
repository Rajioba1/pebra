"""StatusHeader — the Observatory's instrument line: repo, HEAD, store-chain integrity, count.

Chain integrity is labelled "store chain" because it is database-global (not repo-scoped). CodeGraph
health is deliberately NOT shown here — it is not a store read and must not be polled on this surface.
"""

from __future__ import annotations

from textual.widgets import Static

from pebra.tui.widgets.ledger_table import short_commit


def format_status(*, repo_id: str, latest_commit: str | None, chain_valid: bool, total: int) -> str:
    # HEAD = the most recent assessment's assessed_commit (the repo HEAD captured at assess time),
    # derived from the store — never a live git call. Chain integrity is labelled "store chain"
    # because it is database-global, not repo-scoped.
    head = short_commit(latest_commit)
    chain = "ok" if chain_valid else "BROKEN"
    plural = "assessment" if total == 1 else "assessments"
    return f"repo {repo_id}   ·   HEAD {head}   ·   store chain {chain}   ·   {total} {plural}"


class StatusHeader(Static):
    def update_status(
        self, *, repo_id: str, latest_commit: str | None, chain_valid: bool, total: int
    ) -> None:
        self.status_text = format_status(
            repo_id=repo_id, latest_commit=latest_commit, chain_valid=chain_valid, total=total
        )
        self.update(self.status_text)
