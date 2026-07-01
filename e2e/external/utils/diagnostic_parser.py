"""Parse real ``dotnet build`` diagnostics into structured records + compute the edit-attributable delta.

Phase 1 (attribution) is e2e-side only and PROVENANCE ONLY — this module never imports pebra and never
influences any score. It turns raw compiler output into ``Diagnostic`` rows so the graph resolver can
map a diagnostic to a node/edge, and it enforces the DELTA-ONLY honesty invariant: only diagnostics that
are NEW relative to the clean-tree baseline are attributable to the edit.

Symbol extraction targets the two contract-break codes:
  - CS0535: `'Impl' does not implement interface member 'IFace.Member()'`  -> broken class + contract.
  - CS7036: `... required formal parameter 'p' of 'IFace.Member(T)'`        -> contract only (caller side).
For both, ``contract_type`` is the interface TYPE (member stripped) — the class/interface-level signal
the ``implements`` edge is resolved against (WorkspaceViewModel --implements--> IWorkspace). Pure stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# file(line,col): error CSxxxx: message      (message may carry a trailing [..csproj] MSBuild suffix)
_LINE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+(?P<code>CS\d+):\s+(?P<msg>.+?)\s*$"
)
_CSPROJ_SUFFIX = re.compile(r"\s*\[[^\]]*\.csproj\]\s*$")
_CS0535 = re.compile(r"'(?P<broken>[^']+)' does not implement interface member '(?P<contract>[^']+)'")
_CS7036 = re.compile(r"required formal parameter '[^']+' of '(?P<contract>[^']+)'")


@dataclass(frozen=True)
class Diagnostic:
    file: str  # repo-relative POSIX path
    line: int  # 1-based
    col: int  # 1-based
    code: str  # e.g. "CS0535"
    message: str
    broken_symbol: str | None = None  # the implementer that failed the contract (CS0535)
    contract_symbol: str | None = None  # the interface member, e.g. "IWorkspace.CanCloseAsync()"
    contract_type: str | None = None  # the interface TYPE, e.g. "IWorkspace" (implements-edge target)


def _rel_posix(raw_file: str, repo_root: str) -> str:
    f = raw_file.strip().replace("\\", "/")
    root = str(repo_root).strip().replace("\\", "/").rstrip("/")
    if root and f.lower().startswith(root.lower() + "/"):
        f = f[len(root) + 1 :]
    return f.lstrip("/")


def _interface_type(contract: str | None) -> str | None:
    if not contract:
        return None
    before_params = contract.split("(", 1)[0]  # drop the (arg,...) part
    if "." not in before_params:
        return None
    return before_params.rsplit(".", 1)[0]  # strip the member -> the declaring type


def _extract_symbols(code: str, message: str) -> tuple[str | None, str | None]:
    if code == "CS0535":
        m = _CS0535.search(message)
        if m:
            return m.group("broken"), m.group("contract")
    elif code == "CS7036":
        m = _CS7036.search(message)
        if m:
            return None, m.group("contract")
    return None, None


def parse_diagnostics(output: str, repo_root: str) -> list[Diagnostic]:
    """Parse every ``error CSxxxx`` line in combined stdout+stderr into a ``Diagnostic``."""
    diags: list[Diagnostic] = []
    for raw in output.splitlines():
        m = _LINE.match(raw.strip())
        if not m:
            continue
        code = m.group("code")
        message = _CSPROJ_SUFFIX.sub("", m.group("msg")).strip()
        broken, contract = _extract_symbols(code, message)
        diags.append(
            Diagnostic(
                file=_rel_posix(m.group("file"), repo_root),
                line=int(m.group("line")),
                col=int(m.group("col")),
                code=code,
                message=message,
                broken_symbol=broken,
                contract_symbol=contract,
                contract_type=_interface_type(contract),
            )
        )
    return diags


def diagnostics_as_keyset(diags: list[Diagnostic]) -> frozenset[tuple[str, int, int, str]]:
    """The identity key of each diagnostic — the baseline set the delta is computed against."""
    return frozenset((d.file, d.line, d.col, d.code) for d in diags)


def compute_delta(
    post: list[Diagnostic], baseline_keys: frozenset[tuple[str, int, int, str]]
) -> list[Diagnostic]:
    """Only diagnostics NOT present in the baseline are attributable to the edit (delta-only honesty)."""
    return [d for d in post if (d.file, d.line, d.col, d.code) not in baseline_keys]
