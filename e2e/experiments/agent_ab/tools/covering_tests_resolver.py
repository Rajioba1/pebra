"""covering_tests_resolver (P4, e2e-side) — discover a candidate's covering tests from the graph.

Answers "which tests exercise the edited owner?" by a graph CALLER-QUERY over the repo's own CodeGraph
index (which test-file symbols call/reference the edited file's owners) plus a dumb PATH heuristic
(``*Test*.cs`` / a ``tests`` dir / a ``*Tests.csproj`` ancestor). Returns ``(test_project, test_filter)``
for ``candidate_verifier`` to run, or ``(None, None)`` → the verifier reports ``unavailable`` (never a
fabricated pass).

NON-CONTAMINATION is STRUCTURAL, not a reminder: this function's signature accepts only
``repo_path``/``target_file``/``patch_text`` — it has NO access to ``TaskSpec`` and therefore CANNOT
read the hidden ``evaluator_test_project``/``evaluator_test_filter`` grading parameters. It derives the
covering tests independently, so a repair-vs-pebra advantage can never be a leak of the answer key.

Reads the CodeGraph SQLite DB read-only; no ``import pebra`` (enforced e2e boundary).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from e2e.external.utils import graph_resolver as gr

_CALLABLE_KINDS = ("function", "method", "class", "struct", "interface")
_CALLER_EDGE_KINDS = ("calls", "references")


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return "test" in Path(p).name or "/tests/" in p or "/test/" in p


def _nearest_csproj(repo_path: Path, rel_test_file: str) -> str | None:
    """Walk up from a test file to its nearest ancestor ``*.csproj`` (repo-relative)."""
    cur = (repo_path / rel_test_file).resolve().parent
    root = repo_path.resolve()
    while True:
        projects = sorted(cur.glob("*.csproj"))
        if projects:
            try:
                return projects[0].resolve().relative_to(root).as_posix()
            except ValueError:
                return None
        if cur == root or root not in cur.parents:
            return None
        cur = cur.parent


def find_covering_tests(
    repo_path: Path | str, target_file: str, patch_text: str
) -> tuple[str | None, str | None]:
    """Return (test_project, test_filter) covering the owners in ``target_file``, or (None, None).
    ``patch_text`` is accepted for parity/future line-scoping; the current heuristic covers all owners
    in the edited file (broad and safe). Fail-soft: any DB/query failure returns (None, None)."""
    root = Path(repo_path)
    db = gr.find_codegraph_db(root)
    if db is None:
        return (None, None)
    rel = target_file.replace("\\", "/").lstrip("/")
    try:
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return (None, None)
    try:
        con.row_factory = sqlite3.Row
        kind_ph = ",".join("?" * len(_CALLABLE_KINDS))
        owner_ids = [
            r["id"] for r in con.execute(
                f"SELECT id FROM nodes WHERE file_path = ? AND kind IN ({kind_ph})",
                (rel, *_CALLABLE_KINDS),
            ).fetchall()
        ]
        if not owner_ids:
            return (None, None)
        id_ph = ",".join("?" * len(owner_ids))
        edge_ph = ",".join("?" * len(_CALLER_EDGE_KINDS))
        caller_files = {
            str(r["f"]) for r in con.execute(
                f"SELECT DISTINCT src.file_path AS f FROM edges e JOIN nodes src ON src.id = e.source "
                f"WHERE e.target IN ({id_ph}) AND e.kind IN ({edge_ph}) AND src.file_path IS NOT NULL",
                (*owner_ids, *_CALLER_EDGE_KINDS),
            ).fetchall()
        }
    except (sqlite3.Error, OSError):
        return (None, None)
    finally:
        con.close()

    test_files = sorted(f for f in caller_files if _is_test_path(f))
    for tf in test_files:
        project = _nearest_csproj(root, tf)
        if project is not None:
            return (project, None)  # run the whole matched test project (safe/broad); no filter
    return (None, None)
