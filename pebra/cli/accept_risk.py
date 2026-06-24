"""`pebra accept-risk` (Architecture AD-26) — surface to create a controlled-high-risk sanction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pebra.adapters.repository_registry import RepositoryRegistry
from pebra.adapters.sanction_store import SanctionStore
from pebra.adapters.store.db import SqliteStore
from pebra.app import accept_risk_controller


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "accept-risk", help="Create a controlled-high-risk sanction from a JSON spec."
    )
    p.add_argument("sanction_file", help="Path to the sanction spec JSON.")
    p.add_argument("--repo-root", default=None)
    p.add_argument("--db", default=None)
    p.set_defaults(func=run)


def run(args: Any) -> int:
    spec = json.loads(Path(args.sanction_file).read_text(encoding="utf-8"))
    registry = RepositoryRegistry()
    repo = registry.resolve(args.repo_root or ".")
    db_path = args.db or str(Path(repo.repo_root) / ".pebra" / "pebra.db")
    store = SqliteStore(db_path)
    sid = accept_risk_controller.accept_risk(
        repo.repo_id, spec, sanction_port=SanctionStore(store)
    )
    print(json.dumps({"sanction_id": sid, "repo_id": repo.repo_id}, indent=2))
    store.close()
    return 0
