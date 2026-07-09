"""Resolve a compiler diagnostic to a CodeGraph node/edge — the PRIMARY proof being the class/interface
``implements`` edge (e.g. WorkspaceViewModel --implements--> IWorkspace).

Phase 1 attribution, e2e-side only: reads the CodeGraph SQLite DB DIRECTLY (read-only), never imports
pebra (boundary rule). Fail-soft in the codegraph tradition — any missing DB / old schema / sqlite error
degrades to an honest ``unresolved`` result rather than raising, so a broken graph never breaks a run.

Attribution is graded and never fabricated:
  - located_symbol(+implements_edge): the diagnostic location resolves to a node AND the broken class
    implements the named interface — the high-confidence, class/interface-level proof.
  - located_file: the file is in the graph but no symbol span covers the line.
  - symbol_name: only a name match, no location.
  - unresolved: nothing resolved (confidence 0.0, with a fallback_reason).
The method-level (``IWorkspace::CanCloseAsync`` -> class method via a heuristic ``calls`` edge) is a
SECONDARY medium signal (``method_match``), never the primary proof — its graph provenance is heuristic.
Pure stdlib.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import json
from dataclasses import dataclass
from pathlib import Path

_CODEGRAPH_ENGINE = "codegraph"
# Boundary-safe twin of pebra.core.graph_version.CODEGRAPH_DEFAULT_VERSION. Kept in sync by
# tests/unit/test_codegraph_launcher_parity.py because e2e/ may not import pebra.
_CODEGRAPH_DEFAULT_VERSION = "1.1.1"

_CALLABLE_KINDS = ("function", "method", "class", "struct", "interface", "trait", "protocol")
_CALL_EDGE_KINDS = ("calls", "references")

# confidence keyed on (base_locator, implements_edge) — monotone, honest, never > located_symbol+edge.
_CONFIDENCE = {
    ("located_symbol", True): 1.0,
    ("located_symbol", False): 0.9,
    ("located_file", True): 0.75,
    ("located_file", False): 0.7,
    ("symbol_name", True): 0.8,
    ("symbol_name", False): 0.6,
    ("unresolved", True): 0.0,
    ("unresolved", False): 0.0,
}


@dataclass(frozen=True)
class AttributionResult:
    attribution_method: str
    attribution_confidence: float
    implements_edge: bool
    edge_kind: str | None
    method_match: bool
    interface_name: str | None
    edited_symbol_name: str
    broken_node_id: str | None
    graph_freshness: str  # "fresh" | "unknown"
    fallback_reason: str | None = None


def _unresolved(edited_symbol: str, reason: str) -> AttributionResult:
    return AttributionResult(
        attribution_method="unresolved",
        attribution_confidence=0.0,
        implements_edge=False,
        edge_kind=None,
        method_match=False,
        interface_name=None,
        edited_symbol_name=edited_symbol,
        broken_node_id=None,
        graph_freshness="unknown",
        fallback_reason=reason,
    )


def _codegraph_launcher_names() -> tuple[str, ...]:
    # mirrors pebra.core.engine_paths: .cmd then .exe on Windows; bare name on POSIX.
    return (f"{_CODEGRAPH_ENGINE}.cmd", f"{_CODEGRAPH_ENGINE}.exe") if os.name == "nt" else (_CODEGRAPH_ENGINE,)


def _codegraph_launcher_in(bindir: Path) -> str | None:
    for name in _codegraph_launcher_names():
        cand = bindir / name
        if cand.is_file():
            return str(cand)
    return None


def _resolve_codegraph_launcher() -> str | None:
    """Boundary-safe twin of ``pebra.core.engine_paths.find_engine`` for locating the codegraph launcher.

    Mirrors production order so this fallback DB locator honors the same install conventions the CLI uses:
    PEBRA_CODEGRAPH_BIN override (launcher FILE or bin DIR) -> PATH -> PEBRA's pinned managed install.
    Pinned to find_engine by tests/unit/test_codegraph_launcher_parity.py. Pure stdlib.
    """
    override = os.environ.get("PEBRA_CODEGRAPH_BIN", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        if p.is_dir():
            hit = _codegraph_launcher_in(p)
            if hit:
                return hit
        # misconfigured override -> fall through to PATH / managed install
    found = shutil.which(_CODEGRAPH_ENGINE)
    if found:
        return found
    return _codegraph_launcher_in(Path.home() / ".codegraph" / "pebra" / _CODEGRAPH_DEFAULT_VERSION / "bin")


def find_codegraph_db(repo_root: Path, hint_db_path: Path | None = None) -> Path | None:
    """Best-effort DB discovery. The e2e conftest supplies the explicit path; this is the fallback."""
    if hint_db_path and Path(hint_db_path).is_file():
        return Path(hint_db_path)
    cg = _resolve_codegraph_launcher()
    if cg:
        try:
            proc = subprocess.run(
                [cg, "status", str(repo_root), "--json"], capture_output=True, text=True, timeout=60
            )
            if proc.returncode == 0:
                payload = json.loads(proc.stdout)
                index_path = payload.get("indexPath") if isinstance(payload, dict) else None
                if isinstance(index_path, str) and index_path:
                    db = Path(index_path) / "codegraph.db"
                    if db.is_file():
                        return db
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
    fallback = Path(repo_root) / ".codegraph" / "codegraph.db"
    return fallback if fallback.is_file() else None


def _name_variants(symbol: str) -> tuple[str, ...]:
    dot = symbol.replace("::", ".")
    leaf = dot.rsplit(".", 1)[-1]
    return tuple(dict.fromkeys((symbol, dot, leaf)))  # dedup, order-preserving


def _node_by_name(con: sqlite3.Connection, name: str | None) -> str | None:
    if not name:
        return None
    for variant in _name_variants(name):
        row = con.execute(
            "SELECT id FROM nodes WHERE qualified_name = ? OR name = ? LIMIT 1", (variant, variant)
        ).fetchone()
        if row:
            return row[0]
    return None


def _implements_edge(con: sqlite3.Connection, a: str, b: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM edges WHERE kind = 'implements' AND "
        "((source = ? AND target = ?) OR (source = ? AND target = ?)) LIMIT 1",
        (a, b, b, a),
    ).fetchone()
    return row is not None


def _method_match(con: sqlite3.Connection, edited_node: str | None, broken_symbol: str | None,
                  broken_file: str) -> bool:
    """SECONDARY heuristic: does the edited symbol reach a same-named method in the broken class via a
    (heuristic) calls/references edge? Never the primary proof."""
    if not edited_node:
        return False
    placeholders = ",".join("?" for _ in _CALL_EDGE_KINDS)
    rows = con.execute(
        f"SELECT target FROM edges WHERE source = ? AND kind IN ({placeholders})",
        (edited_node, *_CALL_EDGE_KINDS),
    ).fetchall()
    for (target_id,) in rows:
        node = con.execute(
            "SELECT file_path, qualified_name FROM nodes WHERE id = ? LIMIT 1", (target_id,)
        ).fetchone()
        if not node:
            continue
        file_path, qualified = node
        if file_path == broken_file:
            return True
        if broken_symbol and qualified and broken_symbol in qualified:
            return True
    return False


def resolve_diagnostic(diag, edited_symbol: str, db_path: Path) -> AttributionResult:
    db = Path(db_path)
    if not db.is_file():
        return _unresolved(edited_symbol, f"codegraph db not found: {db}")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return _unresolved(edited_symbol, f"cannot open codegraph db: {exc}")
    try:
        ver = con.execute("SELECT MAX(version) FROM schema_versions").fetchone()
        if not ver or ver[0] is None or int(ver[0]) < 5:
            return _unresolved(edited_symbol, f"codegraph schema below v5: {ver and ver[0]}")

        # base locator: tightest enclosing callable at (file, line)
        placeholders = ",".join("?" for _ in _CALLABLE_KINDS)
        loc = con.execute(
            f"SELECT id FROM nodes WHERE file_path = ? AND start_line <= ? AND end_line >= ? "
            f"AND kind IN ({placeholders}) ORDER BY (end_line - start_line) ASC LIMIT 1",
            (diag.file, diag.line, diag.line, *_CALLABLE_KINDS),
        ).fetchone()
        file_present = con.execute(
            "SELECT 1 FROM nodes WHERE file_path = ? LIMIT 1", (diag.file,)
        ).fetchone()
        class_node = _node_by_name(con, diag.broken_symbol)

        if loc:
            base, broken_node = "located_symbol", loc[0]
        elif file_present:
            base, broken_node = "located_file", class_node  # loc is None here; class_node or None
        elif class_node:
            base, broken_node = "symbol_name", class_node
        else:
            return _unresolved(edited_symbol, f"file not found in graph: {diag.file}")

        iface_node = _node_by_name(con, diag.contract_type)
        edge_class = class_node or broken_node
        implements = bool(edge_class and iface_node and _implements_edge(con, edge_class, iface_node))

        edited_node = _node_by_name(con, edited_symbol)
        method_match = _method_match(con, edited_node, diag.broken_symbol, diag.file)

        method = base + ("+implements_edge" if implements else "")
        return AttributionResult(
            attribution_method=method,
            attribution_confidence=_CONFIDENCE[(base, implements)],
            implements_edge=implements,
            edge_kind="implements" if implements else None,
            method_match=method_match,
            interface_name=diag.contract_type,
            edited_symbol_name=edited_symbol,
            broken_node_id=broken_node,
            graph_freshness="fresh",
            fallback_reason=None,
        )
    except (sqlite3.Error, OSError) as exc:
        return _unresolved(edited_symbol, f"codegraph query failed: {exc}")
    finally:
        con.close()


def resolve_diagnostics(diags, edited_symbol: str, db_path: Path) -> tuple[list, int]:
    results = [resolve_diagnostic(d, edited_symbol, db_path) for d in diags]
    unresolved = sum(1 for r in results if r.attribution_method == "unresolved")
    return results, unresolved


def assemble_graph_attribution(results, *, diags, predicted_dependents: int,
                               unresolved_count: int) -> dict:
    """Aggregate provenance blob for the outcome ``detail``. Provenance only — never scored."""
    idx = next((i for i, r in enumerate(results) if r.attribution_method != "unresolved"), 0)
    primary = results[idx] if results else _unresolved(edited_symbol="", reason="no diagnostics")
    pdiag = diags[idx] if diags else None
    return {
        "error_kind": "compiler",
        "diagnostic": pdiag.code if pdiag else None,
        "broken_file": pdiag.file if pdiag else None,
        "broken_line": pdiag.line if pdiag else None,
        "broken_symbol": pdiag.broken_symbol if pdiag else None,
        "interface": primary.interface_name,
        "edited_symbol": primary.edited_symbol_name,
        "edge_kind": primary.edge_kind,
        "implements_edge": primary.implements_edge,
        "method_match": primary.method_match,
        # honest wording: callers (fan-in) predicted vs broken FILES materialized — NOT a subset match.
        "predicted_callers": predicted_dependents,
        "actual_broken_files": len({d.file for d in diags}) if diags else 0,
        "attribution_method": primary.attribution_method,
        "attribution_confidence": primary.attribution_confidence,
        "unresolved_count": unresolved_count,
        "graph_freshness": primary.graph_freshness,
    }
