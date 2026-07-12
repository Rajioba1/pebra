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

import inspect
import re
import subprocess
import tempfile
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
_WRITE_PROTECTED_DIRS = _HIDDEN_DIRS | {".agent-instructions"}
_REDACTION = "[redacted]"
_PATCH_PATH_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_PATCH_OLD_RE = re.compile(r"^--- (.+)$")
_PATCH_NEW_RE = re.compile(r"^\+\+\+ (.+)$")
_PATCH_RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
_PATCH_RENAME_TO_RE = re.compile(r"^rename to (.+)$")
_PATCH_COPY_FROM_RE = re.compile(r"^copy from (.+)$")
_PATCH_COPY_TO_RE = re.compile(r"^copy to (.+)$")
_UNSUPPORTED_PATCH_MODE_RE = re.compile(
    r"^(?:old mode|new mode)\s+|^new file mode\s+(?!100644\s*$)", re.MULTILINE
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


def _contains_write_protected_part(path: Path) -> bool:
    return any(part in _WRITE_PROTECTED_DIRS for part in path.parts)


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
    if _contains_write_protected_part(target.relative_to(repo_root.resolve())):
        return {"error": "hidden path requested"}
    try:
        if target.is_file() and target.stat().st_size > _MAX_READ_BYTES:
            return {"error": "existing file is too large to replace safely; use edit_file"}
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        return {"error": model_safe_text(f"write failed for {path!r}: {detail}")}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        return {"error": model_safe_text(f"write failed for {path!r}: {detail}")}
    return {"ok": True}


def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    repo_root: Path,
    *,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Replace one unique string, or every match when explicitly requested."""
    try:
        target = _resolve_guarded(path, repo_root)
    except PathTraversalError:
        return {"error": "path escapes repo boundary"}
    if _contains_write_protected_part(target.relative_to(repo_root.resolve())):
        return {"error": "hidden path requested"}
    if not old_string:
        return {"error": "old_string must not be empty"}
    if not target.is_file():
        return {"error": model_safe_text(f"not a file: {path}")}
    try:
        content = target.read_bytes()
        old = old_string.encode("utf-8")
        new = new_string.encode("utf-8")
        matches = content.count(old)
        if matches == 0:
            return {"error": "old_string was not found"}
        if not replace_all and matches != 1:
            return {"error": "old_string is not unique; set replace_all to true"}
        target.write_bytes(content.replace(old, new, -1 if replace_all else 1))
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        return {"error": model_safe_text(f"edit failed for {path!r}: {detail}")}
    return {"ok": True}


def _safe_patch_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    return bool(normalized) and not path.is_absolute() and ":" not in normalized and ".." not in path.parts


def _validated_patch_paths(patch_text: str) -> list[str] | None:
    paths: list[str] = []
    current: tuple[str, str] | None = None
    old_seen = new_seen = False
    for line in (patch_text or "").splitlines():
        if match := _PATCH_PATH_RE.match(line):
            if current is not None and old_seen != new_seen:
                return None
            current = (match.group(1), match.group(2))
            paths.extend(current)
            old_seen = new_seen = False
            continue
        if match := _PATCH_OLD_RE.match(line):
            if current is None or old_seen:
                return None
            value = match.group(1).split("\t", 1)[0]
            if value not in {f"a/{current[0]}", "/dev/null"}:
                return None
            old_seen = True
        elif match := _PATCH_NEW_RE.match(line):
            if current is None or new_seen:
                return None
            value = match.group(1).split("\t", 1)[0]
            if value not in {f"b/{current[1]}", "/dev/null"}:
                return None
            new_seen = True
        elif match := _PATCH_RENAME_FROM_RE.match(line):
            if current is None or match.group(1) != current[0]:
                return None
        elif match := _PATCH_RENAME_TO_RE.match(line):
            if current is None or match.group(1) != current[1]:
                return None
        elif match := _PATCH_COPY_FROM_RE.match(line):
            if current is None or match.group(1) != current[0]:
                return None
        elif match := _PATCH_COPY_TO_RE.match(line):
            if current is None or match.group(1) != current[1]:
                return None
    if current is None or old_seen != new_seen:
        return None
    return paths


def apply_patch(patch_text: str, repo_root: Path) -> dict[str, Any]:
    """Apply one git-style patch atomically inside the confined clone."""
    paths = _validated_patch_paths(patch_text)
    if not paths:
        return {"error": "patch has invalid or undeclared file headers"}
    if any(not _safe_patch_path(path) for path in paths):
        return {"error": "patch contains an unsafe path"}
    if any(_contains_write_protected_part(Path(path)) for path in paths):
        return {"error": "patch contains a protected path"}
    if _UNSUPPORTED_PATCH_MODE_RE.search(patch_text):
        return {"error": "patch contains unsupported file-mode changes"}

    patch_path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix="agent-ab-", suffix=".patch")
        patch_path = Path(raw_path)
        with open(fd, "wb", closefd=True) as stream:
            stream.write(patch_text.encode("utf-8"))
        argv = ["git", "apply", "--whitespace=nowarn", str(patch_path)]
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", str(patch_path)],
            cwd=str(repo_root), capture_output=True, text=True, timeout=60,
        )
        if check.returncode != 0:
            return {"error": "patch does not apply cleanly"}
        applied = subprocess.run(
            argv, cwd=str(repo_root), capture_output=True, text=True, timeout=60,
        )
        if applied.returncode != 0:
            return {"error": "patch could not be applied"}
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return {"error": model_safe_text(f"patch failed: {type(exc).__name__}")}
    finally:
        if patch_path is not None:
            patch_path.unlink(missing_ok=True)
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
    sln: str = "TemplateBlueprint.sln", timeout_seconds: float | None = None,
) -> dict[str, Any]:
    backend = backend or CSharpBackend()
    spec = spec or type("_Spec", (), {"build_solution": sln})()
    if timeout_seconds is not None:
        spec = _TimeoutSpec(spec, timeout_seconds)
    r = backend.run_build(repo_root, spec)
    return {"available": r.available, "passed": r.passed,
            "error_summary": model_safe_text(r.error_summary)}


def run_tests(
    repo_root: Path, *, backend: Any | None = None, spec: Any | None = None,
    sln: str = "TemplateBlueprint.sln", timeout_seconds: float | None = None,
) -> dict[str, Any]:
    backend = backend or CSharpBackend()
    spec = spec or type("_Spec", (), {"build_solution": sln})()
    if timeout_seconds is not None:
        spec = _TimeoutSpec(spec, timeout_seconds)
    r = backend.run_tests(repo_root, spec)
    return {"available": r.available, "passed": r.passed,
            "error_summary": model_safe_text(r.error_summary)}


class _TimeoutSpec:
    def __init__(self, wrapped: Any, timeout_seconds: float) -> None:
        self._wrapped = wrapped
        self.command_timeout = max(1, int(timeout_seconds))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def advisory_check(
    payload: dict[str, Any],
    advisory_backend: Callable[..., dict[str, Any]],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Dispatch to the arm's backend (bound in run_pair) and coerce to the shared, arm-neutral shape."""
    missing = [k for k in advisory_contract.INPUT_SCHEMA["required"] if not payload.get(k)]
    if not payload.get("proposed_patch") and not payload.get("candidate_edits"):
        missing.append("proposed_patch or candidate_edits")
    if missing:
        return advisory_contract.normalize_output({
            "recommended_decision": None,
            "risk_level": "unknown",
            "advisory": ("The advisory could not run because required pre-edit fields were missing. "
                         "Provide target_file, change_summary, and proposed_patch or candidate_edits."),
            "detail": {},
        })
    try:
        parameters = inspect.signature(advisory_backend).parameters.values()
        accepts_timeout = any(
            parameter.name == "timeout_seconds"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        raw = (
            advisory_backend(payload, timeout_seconds=timeout_seconds)
            if accepts_timeout
            else advisory_backend(payload)
        )
    except Exception:
        return advisory_contract.normalize_output({
            "recommended_decision": None,
            "risk_level": "unknown",
            "advisory": "The advisory tool is temporarily unavailable. Continue with normal code review and tests.",
            "detail": {},
        })
    return advisory_contract.normalize_output(raw)
