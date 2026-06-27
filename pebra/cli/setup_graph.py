"""`pebra setup-graph` / `pebra doctor` — graph-engine maintenance (Architecture §3, M5c.5).

These are EXPLICIT operator actions, deliberately separate from `pebra assess`. They may install /
initialize / index / repair the graph engine (a machine-mutating setup step). `pebra assess`, by
contrast, never installs and only does a safe repair-sync of an *existing* index — so risk assessment
never silently mutates the machine.

Product language is "graph engine"; the default provider is codegraph (config `graph_provider`). This
surface shells out to the engine with stdlib subprocess only and imports neither the assess path nor the
codegraph adapter — it is a thin maintenance wrapper.

Worktree rule (strict one-worktree-one-index): a `worktreeMismatch` means the resolved index belongs to
another worktree. The fix is a *worktree-local* index — `codegraph init <worktree>` (the legacy `-i`
flag is deprecated; init indexes by default) — NOT a sync, which would refresh the wrong index.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

_ENGINE = "codegraph"
_INSTALL_CMD = ["npm", "install", "-g", "@colbymchenry/codegraph"]
_INSTALL_HINT = "install Node.js + npm, then run: npm install -g @colbymchenry/codegraph"


def register(subparsers: Any) -> None:
    sg = subparsers.add_parser(
        "setup-graph", help="Install/initialize the graph engine index for this repo/worktree."
    )
    sg.add_argument("--repo-root", default=".")
    sg.add_argument(
        "--fix", action="store_true",
        help="Repair a worktree mismatch by building a worktree-local index.",
    )
    sg.add_argument("--json", action="store_true", dest="as_json")
    sg.set_defaults(func=run_setup_graph)

    dr = subparsers.add_parser(
        "doctor", help="Diagnose (read-only) the graph engine; --fix-graph to repair this worktree."
    )
    dr.add_argument("--repo-root", default=".")
    dr.add_argument("--fix-graph", action="store_true", help="Repair the graph index for this worktree.")
    dr.add_argument("--json", action="store_true", dest="as_json")
    dr.set_defaults(func=run_doctor)


# --- engine shell helpers (stdlib only) ---

def _installed() -> bool:
    return shutil.which(_ENGINE) is not None


def _run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found on PATH"
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - defensive
        return 1, "", str(exc)


def _status(repo_root: str) -> dict[str, Any] | None:
    rc, out, _ = _run([_ENGINE, "status", repo_root, "--json"], timeout=30)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _healthy(status: dict[str, Any] | None) -> bool:
    if not status or status.get("initialized") is False or status.get("worktreeMismatch"):
        return False
    pending = status.get("pendingChanges") or {}
    has_pending = any(pending.get(k) for k in ("added", "modified", "removed"))
    reindex = bool((status.get("index") or {}).get("reindexRecommended"))
    return not has_pending and not reindex


def _diagnosis(status: dict[str, Any] | None) -> dict[str, Any]:
    if status is None:
        return {"initialized": False, "worktree_mismatch": False, "healthy": False,
                "detail": "no status (engine errored or repo not initialized)"}
    return {
        "initialized": status.get("initialized") is not False,
        "worktree_mismatch": bool(status.get("worktreeMismatch")),
        "pending_changes": status.get("pendingChanges"),
        "reindex_recommended": bool((status.get("index") or {}).get("reindexRecommended")),
        "healthy": _healthy(status),
    }


def _emit(payload: dict[str, Any], as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("\n".join(lines))


def _ensure_installed(as_json: bool, *, allow_install: bool) -> int | None:
    """Return an error code if the engine is unavailable and could not be installed, else None."""
    if _installed():
        return None
    if allow_install and shutil.which("npm") is not None:
        rc, _, err = _run(_INSTALL_CMD, timeout=600)
        if rc == 0 and _installed():
            return None
        _emit({"ok": False, "step": "install", "error": err.strip()}, as_json,
              [f"graph engine install failed; run manually: {' '.join(_INSTALL_CMD)}", err.strip()])
        return 1
    _emit({"ok": False, "step": "install", "error": "engine not found"}, as_json,
          [f"graph engine '{_ENGINE}' not found; {_INSTALL_HINT}"])
    return 1


def _build_worktree_local_index(repo_root: str) -> dict[str, Any] | None:
    """init (worktree-local; indexes by default) + sync, then return the fresh status."""
    _run([_ENGINE, "init", repo_root], timeout=600)
    _run([_ENGINE, "sync", repo_root], timeout=300)
    return _status(repo_root)


def run_setup_graph(args: Any) -> int:
    repo = args.repo_root
    err = _ensure_installed(args.as_json, allow_install=True)
    if err is not None:
        return err
    status = _build_worktree_local_index(repo)
    diag = _diagnosis(status)
    ok = diag["healthy"]
    intent = "repair worktree-local index" if args.fix else "initialize graph index"
    lines = [f"setup-graph ({intent}) — repo: {repo}",
             f"  initialized:       {diag.get('initialized')}",
             f"  worktree_mismatch: {diag.get('worktree_mismatch')}",
             f"  healthy:           {ok}"]
    if not ok and diag.get("worktree_mismatch"):
        lines.append("  worktree mismatch persists — ensure you run this inside the target worktree.")
    _emit({"ok": ok, "command": "setup-graph", "repo_root": repo, "diagnosis": diag}, args.as_json, lines)
    return 0 if ok else 1


def run_doctor(args: Any) -> int:
    repo = args.repo_root
    if not _installed():
        _emit({"ok": False, "step": "engine", "error": "not found"}, args.as_json,
              [f"graph engine '{_ENGINE}' not found; {_INSTALL_HINT}"])
        return 1
    status = _status(repo)
    diag = _diagnosis(status)
    repaired = False
    if args.fix_graph and not diag["healthy"]:
        status = _build_worktree_local_index(repo)
        diag = _diagnosis(status)
        repaired = True
    ok = diag["healthy"]
    lines = [f"doctor — repo: {repo}",
             f"  initialized:       {diag.get('initialized')}",
             f"  worktree_mismatch: {diag.get('worktree_mismatch')}",
             f"  reindex_needed:    {diag.get('reindex_recommended')}",
             f"  healthy:           {ok}"]
    if not ok and not args.fix_graph:
        lines.append("  run `pebra doctor --fix-graph` (or `pebra setup-graph --fix`) to repair.")
    _emit({"ok": ok, "command": "doctor", "repo_root": repo, "repaired": repaired, "diagnosis": diag},
          args.as_json, lines)
    return 0 if ok else 1
