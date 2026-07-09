"""Manual utility: point `pebra dashboard` at one of the PEBRA stores an assay run left behind.

Only the ``pebra`` / ``pebra_graph_repair`` arms write a store (they call `pebra assess`); this lists the
``pebra.db`` files under ``e2e/out/ab/<run-id>/``, labels each by its isolated clone dir, resolves the
sibling ``repo/``, and prints the ready-to-run ``pebra dashboard ... --port <p> --open`` command. It is a
CLI convenience, NOT a gated test, and never imports pebra (the printed command shells it).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

_AB_OUT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def repo_id_for(repo_root: str) -> str:
    """Boundary-safe twin of production ``RepositoryRegistry.resolve(...).repo_id`` (the stable repo_id
    hash). Pinned to production by ``tests/unit/test_observatory_repo_id_parity.py`` so it can't drift —
    a drifted repo_id would make the dashboard filter on the wrong id and silently render an empty repo.

    Used to serve the dashboard against a repo_id WITHOUT CLI ``--repo-root`` resolution (which would init
    a ``.pebra/`` dir inside the assay clone)."""
    root = Path(repo_root).resolve()
    return "repo_" + hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]


def _run_root(run_id: str, *, ab_out: Path | None = None) -> Path:
    """Resolve a run id under the assay output root, rejecting path-like input."""
    if not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run-id must be a simple run directory name")
    return (ab_out or _AB_OUT) / run_id


def list_run_dbs(run_id: str, *, ab_out: Path | None = None) -> list[dict]:
    """Direct child ``pebra.db`` stores under the run dir, with clone label + sibling repo."""
    root = _run_root(run_id, ab_out=ab_out)
    dbs: list[dict] = []
    if not root.is_dir():
        return dbs
    for db in sorted(root.glob("*/pebra.db")):
        repo = db.parent / "repo"
        dbs.append({
            "clone": db.parent.name,          # <task>_seed<n>_<arm_token> (arm is intentionally opaque)
            "db": str(db),
            "repo": str(repo) if repo.is_dir() else None,
        })
    return dbs


def dashboard_command(repo: str, db: str, port: int) -> list[str]:
    # --read-only + --repo-id + --db, and deliberately NO --repo-root: the CLI's --repo-root path calls
    # RepositoryRegistry.resolve(), which initializes a .pebra/ dir INSIDE the assay clone. This direct
    # command is a fallback; the observatory Open button is stricter because it serves a temp db copy.
    return [sys.executable, "-m", "pebra", "dashboard", "--db", db, "--repo-id", repo_id_for(repo),
            "--read-only", "--port", str(port), "--open"]


def render_command(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Launch pebra dashboard against an assay run's PEBRA store.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--port", type=int, default=4500)
    p.add_argument("--index", type=int, default=0, help="Which listed store to open (default 0).")
    args = p.parse_args(argv)

    try:
        dbs = list_run_dbs(args.run_id)
    except ValueError as exc:
        print(str(exc))
        return 1
    if not dbs:
        print(f"no pebra.db under e2e/out/ab/{args.run_id}/ "
              "(only the pebra / pebra_graph_repair arms write one)")
        return 1
    for i, d in enumerate(dbs):
        print(f"[{i}] {d['clone']}  db={d['db']}  repo={'yes' if d['repo'] else 'MISSING'}")
    chosen = dbs[args.index] if 0 <= args.index < len(dbs) else None
    if chosen is None or not chosen["repo"]:
        print("selected store has no sibling repo/ dir; cannot derive --repo-id")
        return 1
    cmd = dashboard_command(chosen["repo"], chosen["db"], args.port)
    print("\nrun this (opens the browser):\n  " + render_command(cmd))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
