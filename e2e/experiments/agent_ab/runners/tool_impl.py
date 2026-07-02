"""Tool implementations served to the subject agent — the LOGGED, CONFINED "hands".

Every action the agent can take goes through one of these functions, so (a) the diff/adherence are
captured, (b) the agent can never touch the filesystem or shell outside the isolated clone, and (c)
blinding is controllable. File tools are confined to the clone by ``_resolve_guarded`` (path-traversal
fails closed). Build/test go through ``dotnet_harness`` (fixed argv, no shell). Search is python-native
(no ``rg`` dependency). Tool errors are RETURNED as ``{"error": ...}`` (not raised) so the agent gets a
coherent response and can react — except a traversal attempt, which is captured as an error result too.
No pebra import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab.tools import advisory_contract
from e2e.external.utils import dotnet_harness as dn

_MAX_READ_BYTES = 64_000
_MAX_LIST_ENTRIES = 500
_MAX_MATCHES = 200
_MAX_GREP_FILE_BYTES = 1_000_000


class PathTraversalError(ValueError):
    """A tool path resolved outside the repo clone boundary."""


def _resolve_guarded(path: str, repo_root: Path) -> Path:
    """Resolve ``path`` under ``repo_root``; raise PathTraversalError if it escapes the clone."""
    root = repo_root.resolve()
    target = (root / (path or ".")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathTraversalError(f"path {path!r} escapes repo boundary {root}") from exc
    return target


def read_file(path: str, repo_root: Path) -> dict[str, Any]:
    try:
        target = _resolve_guarded(path, repo_root)
    except PathTraversalError as exc:
        return {"error": str(exc)}
    if not target.is_file():
        return {"error": f"not a file: {path}"}
    data = target.read_bytes()[:_MAX_READ_BYTES]
    text = data.decode("utf-8", errors="replace")
    if target.stat().st_size > _MAX_READ_BYTES:
        text += "\n[... truncated ...]"
    return {"content": text}


def write_file(path: str, content: str, repo_root: Path) -> dict[str, Any]:
    try:
        target = _resolve_guarded(path, repo_root)
    except PathTraversalError as exc:
        return {"error": str(exc)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": f"write failed: {exc}"}
    return {"ok": True}


def list_dir(path: str | None, repo_root: Path) -> dict[str, Any]:
    try:
        target = _resolve_guarded(path or ".", repo_root)
    except PathTraversalError as exc:
        return {"error": str(exc)}
    if not target.is_dir():
        return {"error": f"not a directory: {path}"}
    root = repo_root.resolve()
    entries = sorted(
        p.relative_to(root).as_posix() + ("/" if p.is_dir() else "")
        for p in target.iterdir()
        if ".git" not in p.parts
    )
    return {"entries": entries[:_MAX_LIST_ENTRIES]}


def search_grep(pattern: str, repo_root: Path, *, path: str | None = None,
                file_glob: str | None = None) -> dict[str, Any]:
    """Python-native recursive line scan (no rg dependency). Returns up to _MAX_MATCHES lines."""
    try:
        root = _resolve_guarded(path or ".", repo_root)
    except PathTraversalError as exc:
        return {"matches": [], "error": str(exc)}
    matches: list[str] = []
    repo = repo_root.resolve()
    files = [root] if root.is_file() else root.rglob(file_glob or "*")
    for fp in files:
        if len(matches) >= _MAX_MATCHES:
            break
        if not fp.is_file() or ".git" in fp.parts:
            continue
        try:
            if fp.stat().st_size > _MAX_GREP_FILE_BYTES:
                continue
            rel = fp.relative_to(repo).as_posix()
            for n, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern in line:
                    matches.append(f"{rel}:{n}:{line.strip()[:200]}")
                    if len(matches) >= _MAX_MATCHES:
                        break
        except OSError:
            continue
    return {"matches": matches}


def run_build(repo_root: Path) -> dict[str, Any]:
    r = dn.run_build(repo_root)
    return {"available": r.available, "passed": r.passed, "error_summary": r.error_summary}


def run_tests(repo_root: Path) -> dict[str, Any]:
    r = dn.run_tests(repo_root)
    return {"available": r.available, "passed": r.passed, "error_summary": r.error_summary}


def advisory_check(payload: dict[str, Any], advisory_backend: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Dispatch to the arm's backend (bound in run_pair) and coerce to the shared, arm-neutral shape."""
    missing = [k for k in advisory_contract.INPUT_SCHEMA["required"] if not payload.get(k)]
    if missing:
        return advisory_contract.normalize_output({
            "recommended_decision": None,
            "risk_level": "unknown",
            "advisory": ("The advisory could not run because required pre-edit fields were missing. "
                         "Provide target_file, change_summary, and proposed_patch."),
            "detail": {},
        })
    raw = advisory_backend(payload)
    return advisory_contract.normalize_output(raw)
