"""Tool implementations served to the subject agent — the LOGGED, CONFINED "hands".

Every action the agent can take goes through one of these functions, so (a) the diff/adherence are
captured, (b) the agent can never touch the filesystem or shell outside the isolated clone, and (c)
blinding is controllable. File tools are confined to the clone by ``_resolve_guarded`` (path-traversal
fails closed). Build/test go through the arm's fixed build backend. Search is python-native
(no ``rg`` dependency). Tool errors are RETURNED as ``{"error": ...}`` (not raised) so the agent gets a
coherent response and can react — except a traversal attempt, which is captured as an error result too.
No pebra import.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab.forbidden import EXPERIMENT_LEAK_TERMS
from e2e.experiments.agent_ab.backends import CSharpBackend
from e2e.experiments.agent_ab.tools import advisory_contract

_MAX_READ_BYTES = 64_000
_MAX_LIST_ENTRIES = 500
_MAX_MATCHES = 200
_MAX_GREP_FILE_BYTES = 1_000_000
_HIDDEN_DIRS = {".git", ".codegraph", ".pebra"}
_REDACTION = "[redacted]"
_SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".kt", ".php", ".py", ".rb", ".rs", ".scala", ".swift", ".ts", ".tsx", ".vb",
}
_SOURCE_CODE_RE = re.compile(
    r"\b(async|await|class|const|def|export|for|function|if|import|interface|let|namespace|"
    r"private|public|return|type|using|var|while)\b|[{};=<>()]"
)
_COMMAND_LINE_RE = re.compile(
    r"^\s*(?:\$|git|npm|pnpm|yarn|node|python|pip|dotnet|cargo|go|make|pytest)\b",
    re.IGNORECASE,
)
_SHELL_MUTATION_RE = re.compile(r"\bgit\s+(?:checkout|reset|clean|apply)\b")
_PROSE_OPENING_RE = re.compile(
    r"^\s*(?:here is|here's|i will|we should|to fix|the fix is|this change|first,|then,)\b",
    re.IGNORECASE,
)


def model_safe_text(text: str) -> str:
    """Remove workspace paths and forbidden experiment/engine terms from model-facing harness text."""
    safe = text or ""
    safe = re.sub(r"[A-Za-z]:[\\/][^\s()\"']+", "<path>", safe)
    safe = re.sub(r"(?<!\w)/(?:[^\s()\"']+/)+[^\s()\"']+", "<path>", safe)
    for term in EXPERIMENT_LEAK_TERMS:
        if term.isalpha():
            safe = re.sub(rf"\b{re.escape(term)}\b", _REDACTION, safe, flags=re.IGNORECASE)
        else:
            safe = re.sub(re.escape(term), _REDACTION, safe, flags=re.IGNORECASE)
    return safe


class PathTraversalError(ValueError):
    """A tool path resolved outside the repo clone boundary."""


def _resolve_guarded(path: str, repo_root: Path) -> Path:
    """Resolve ``path`` under ``repo_root``; raise PathTraversalError if it escapes the clone."""
    root = repo_root.resolve()
    target = (root / (path or ".")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathTraversalError(f"path {path!r} escapes repo boundary") from exc
    return target


def _contains_hidden_part(path: Path) -> bool:
    return any(part in _HIDDEN_DIRS for part in path.parts)


def _first_non_comment_line(text: str) -> str:
    """Return the first meaningful leading line, preserving text after a closing block comment."""
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        while line:
            if in_block:
                end = line.find("*/")
                if end < 0:
                    line = ""
                    break
                in_block = False
                line = line[end + 2 :].lstrip()
                continue
            if line.startswith(("//", "#")):
                line = ""
                break
            if line.startswith("/*"):
                end = line.find("*/", 2)
                if end < 0:
                    in_block = True
                    line = ""
                    break
                line = line[end + 2 :].lstrip()
                continue
            return line
    return ""


def _implausible_source_write(content: str, target: Path) -> bool:
    """Catch obvious model prose/command dumps before they replace source files."""
    if target.suffix.lower() not in _SOURCE_EXTENSIONS:
        return False
    text = (content or "").strip()
    if not text:
        return False
    has_code_signal = bool(_SOURCE_CODE_RE.search(text))
    first_line = _first_non_comment_line(text)
    if (
        first_line.lstrip().startswith("```")
        or _COMMAND_LINE_RE.search(first_line)
        or _PROSE_OPENING_RE.search(first_line)
    ):
        return True
    if not has_code_signal and _SHELL_MUTATION_RE.search(text):
        return True
    sentence_count = len(re.findall(r"[.!?](?:\s|$)", text))
    return not has_code_signal and (sentence_count >= 2 or bool(_PROSE_OPENING_RE.search(text)))


def read_file(path: str, repo_root: Path) -> dict[str, Any]:
    try:
        target = _resolve_guarded(path, repo_root)
    except PathTraversalError:
        return {"error": "path escapes repo boundary"}
    if _contains_hidden_part(target.relative_to(repo_root.resolve())):
        return {"error": "hidden path requested"}
    if not target.is_file():
        return {"error": model_safe_text(f"not a file: {path}")}
    data = target.read_bytes()[:_MAX_READ_BYTES]
    text = data.decode("utf-8", errors="replace")
    if target.stat().st_size > _MAX_READ_BYTES:
        text += "\n[... truncated ...]"
    return {"content": text}


def write_file(path: str, content: str, repo_root: Path) -> dict[str, Any]:
    try:
        target = _resolve_guarded(path, repo_root)
    except PathTraversalError:
        return {"error": "path escapes repo boundary"}
    if _contains_hidden_part(target.relative_to(repo_root.resolve())):
        return {"error": "hidden path requested"}
    if _implausible_source_write(content, target):
        return {"error": "write rejected: content does not look like source code for this file type"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        return {"error": model_safe_text(f"write failed for {path!r}: {detail}")}
    return {"ok": True}


def list_dir(path: str | None, repo_root: Path) -> dict[str, Any]:
    try:
        target = _resolve_guarded(path or ".", repo_root)
    except PathTraversalError:
        return {"error": "path escapes repo boundary"}
    if not target.is_dir():
        return {"error": model_safe_text(f"not a directory: {path}")}
    root = repo_root.resolve()
    entries = sorted(
        p.relative_to(root).as_posix() + ("/" if p.is_dir() else "")
        for p in target.iterdir()
        if not _contains_hidden_part(p.relative_to(root))
    )
    return {"entries": entries[:_MAX_LIST_ENTRIES]}


def search_grep(pattern: str, repo_root: Path, *, path: str | None = None,
                file_glob: str | None = None) -> dict[str, Any]:
    """Python-native recursive line scan (no rg dependency). Returns up to _MAX_MATCHES lines."""
    try:
        root = _resolve_guarded(path or ".", repo_root)
    except PathTraversalError:
        return {"matches": [], "error": "path escapes repo boundary"}
    if file_glob and (Path(file_glob).is_absolute() or ".." in Path(file_glob).parts):
        return {"matches": [], "error": "file_glob escapes repo boundary"}
    matches: list[str] = []
    repo = repo_root.resolve()
    try:
        files = [root] if root.is_file() else root.rglob(file_glob or "*")
    except (OSError, ValueError) as exc:
        return {"matches": [], "error": model_safe_text(f"search failed: {type(exc).__name__}")}
    for fp in files:
        if len(matches) >= _MAX_MATCHES:
            break
        try:
            resolved = fp.resolve()
            resolved.relative_to(repo)
            rel_path = resolved.relative_to(repo)
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or _contains_hidden_part(rel_path):
            continue
        try:
            if resolved.stat().st_size > _MAX_GREP_FILE_BYTES:
                continue
            rel = rel_path.as_posix()
            for n, line in enumerate(resolved.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern in line:
                    matches.append(f"{rel}:{n}:{line.strip()[:200]}")
                    if len(matches) >= _MAX_MATCHES:
                        break
        except OSError:
            continue
    return {"matches": matches}


def run_build(
    repo_root: Path, *, backend: Any | None = None, spec: Any | None = None,
    sln: str = "TemplateBlueprint.sln",
) -> dict[str, Any]:
    backend = backend or CSharpBackend()
    spec = spec or type("_Spec", (), {"build_solution": sln})()
    r = backend.run_build(repo_root, spec)
    return {"available": r.available, "passed": r.passed,
            "error_summary": model_safe_text(r.error_summary)}


def run_tests(
    repo_root: Path, *, backend: Any | None = None, spec: Any | None = None,
    sln: str = "TemplateBlueprint.sln",
) -> dict[str, Any]:
    backend = backend or CSharpBackend()
    spec = spec or type("_Spec", (), {"build_solution": sln})()
    r = backend.run_tests(repo_root, spec)
    return {"available": r.available, "passed": r.passed,
            "error_summary": model_safe_text(r.error_summary)}


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
    try:
        raw = advisory_backend(payload)
    except Exception:
        return advisory_contract.normalize_output({
            "recommended_decision": None,
            "risk_level": "unknown",
            "advisory": "The advisory tool is temporarily unavailable. Continue with normal code review and tests.",
            "detail": {},
        })
    return advisory_contract.normalize_output(raw)
