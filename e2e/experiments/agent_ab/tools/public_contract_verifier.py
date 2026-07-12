"""Language-scoped public-contract checks for host-produced candidate verification.

This module derives compatibility requirements only from the submitted patch and materialized
candidate. It has no access to corpus labels or hidden evaluator tests.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_DECLARATION = re.compile(
    r"^\s*export\s+(?:(?:declare|async)\s+)*"
    r"(?P<kind>function|class|interface|type|enum|const|let|var|namespace)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)(?P<tail>.*)$"
)
_EXPORT_LIST = re.compile(r"\bexport\s*(?:type\s*)?\{([^}]*)\}", re.DOTALL)
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")
_VALUE_ALIAS = re.compile(
    r"^\s*export\s+const\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<target>[A-Za-z_$][\w$]*)\s*;\s*$"
)


def _listed_exports(body: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for item in body.split(","):
        item = item.strip()
        if not item:
            continue
        parts = re.split(r"\s+as\s+", item)
        local = parts[0].strip()
        exported = parts[-1].strip()
        if _IDENTIFIER.fullmatch(local) and _IDENTIFIER.fullmatch(exported):
            names[exported] = local
    return names


def _function_signature(match: re.Match[str]) -> str | None:
    if match.group("kind") != "function":
        return None
    return match.group("tail").split("{", 1)[0].strip().rstrip(";")


def _exports_in_source(source: str) -> tuple[dict[str, str | None], dict[str, str]]:
    declarations = {
        match.group("name"): _function_signature(match)
        for line in source.splitlines()
        if (match := _DECLARATION.match(line)) is not None
    }
    aliases: dict[str, str] = {}
    for match in _EXPORT_LIST.finditer(source):
        aliases.update(_listed_exports(match.group(1)))
    for line in source.splitlines():
        if (match := _VALUE_ALIAS.match(line)) is not None:
            aliases[match.group("name")] = match.group("target")
    return declarations, aliases


def _resolved_signature(
    name: str, declarations: dict[str, str | None], aliases: dict[str, str]
) -> str | None:
    """Resolve at most two exact same-file identifier aliases to a function signature."""
    target = name
    visited: set[str] = set()
    for _ in range(3):
        if target in visited:
            return None
        visited.add(target)
        signature = declarations.get(target)
        if signature is not None:
            return signature
        target = aliases.get(target, "")
        if not target:
            return None
    return None


def _removed_exports(patch_text: str) -> tuple[dict[str, dict[str, str | None]], bool]:
    required: dict[str, dict[str, str | None]] = {}
    current_file: str | None = None
    unsupported = False
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].replace("\\", "/")
            continue
        if not current_file or not line.startswith("-") or line.startswith("---"):
            continue
        removed = line[1:]
        declaration = _DECLARATION.match(removed)
        if declaration is not None:
            required.setdefault(current_file, {})[declaration.group("name")] = (
                _function_signature(declaration)
            )
            continue
        listed = _EXPORT_LIST.search(removed)
        if listed is not None:
            for exported in _listed_exports(listed.group(1)):
                required.setdefault(current_file, {})[exported] = None
            continue
        if removed.lstrip().startswith("export "):
            unsupported = True
    return required, unsupported


def check_typescript_public_contract(
    repo_path: Path | str, patch_text: str
) -> tuple[str, tuple[str, ...], str | None]:
    """Return ``(status, contract_failures, reason)`` for a TS/JS candidate.

    ``not_applicable`` means the patch removes no detectable public declaration. Unsupported export
    syntax and unsafe paths return ``unavailable`` so the caller cannot fabricate a pass.
    """
    required, unsupported = _removed_exports(patch_text)
    if unsupported:
        return "unavailable", (), "removed public export uses an unsupported declaration shape"
    if not required:
        return "not_applicable", (), None
    root = Path(repo_path).resolve()
    failures: list[str] = []
    for rel, requirements in sorted(required.items()):
        posix = PurePosixPath(rel)
        if posix.is_absolute() or ".." in posix.parts:
            return "unavailable", (), "public-contract check received an unsafe candidate path"
        candidate = (root / Path(*posix.parts)).resolve()
        try:
            candidate.relative_to(root)
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            return "unavailable", (), f"could not inspect candidate public surface for {rel}"
        declarations, aliases = _exports_in_source(source)
        for name, required_signature in sorted(requirements.items()):
            if name not in declarations and name not in aliases:
                failures.append(f"{rel}::{name} (missing)")
                continue
            candidate_signature = _resolved_signature(name, declarations, aliases)
            if required_signature is not None and candidate_signature != required_signature:
                failures.append(f"{rel}::{name} (signature changed)")
    if failures:
        return (
            "failed",
            tuple(failures),
            "candidate does not preserve existing public contract(s): " + ", ".join(failures),
        )
    return "passed", (), None
