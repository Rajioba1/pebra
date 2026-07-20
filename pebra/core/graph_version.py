"""Graph-engine version policy (M5c.5 / setup-graph) — pure stdlib (``re`` only).

PEBRA pins an EXACT default codegraph version to install (reproducible fan-in, no floating-latest) and
accepts a RUNTIME RANGE it trusts. A version outside the range is untrusted: ``pebra setup-graph``
refuses to install it (without ``--allow-unsupported``), and the assess path routes such a running
binary through Gate 13 (inspect_first under ``require_graph``, provenance warning otherwise).

Lives in ``core`` (stdlib-only) so BOTH the CLI surface (``pebra.cli.setup_graph``) and the adapter
(``pebra.adapters.codegraph_adapter``) can import it without breaching import-linter.

SEMVER NOTE: the accepted band is intentionally minor-locked (``>=A.B,<C.D``) — a codegraph patch
release inside the band can still bump its extraction schema and shift fan-in numbers, so M5d scopes
calibration by the recorded ``index_version`` (extraction version), not by this semver band alone.
"""

from __future__ import annotations

import re

# The exact version `pebra setup-graph` installs by default (never floating-latest).
CODEGRAPH_DEFAULT_VERSION = "1.1.1"
# The runtime versions PEBRA trusts. Minor-locked band; bump (and re-validate) on a minor upgrade.
CODEGRAPH_ACCEPTED_RANGE = ">=1.1,<1.2"

_VER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")
_RANGE = re.compile(r"^\s*>=(\d+)\.(\d+)\s*,\s*<(\d+)\.(\d+)\s*$")
_MAX_VERSION_LENGTH = 64


def is_release_version(version: object) -> bool:
    """Whether a provider version is a bounded lexical release version."""
    return (
        isinstance(version, str)
        and 0 < len(version) <= _MAX_VERSION_LENGTH
        and _VER.fullmatch(version) is not None
    )


def _parse(version: str) -> tuple[int, int, int] | None:
    """Parse 'X.Y[.Z]' (leading 'v' tolerated) into a tuple, or None if unparseable.

    Pre-release/build suffixes are rejected. PEBRA's runtime accept range is for validated release
    versions only; an RC should not silently satisfy the same band as the final release.
    """
    if not is_release_version(version):
        return None
    m = _VER.fullmatch(version)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def in_accepted_range(version: object, range_str: str = CODEGRAPH_ACCEPTED_RANGE) -> bool:
    """True iff ``version`` satisfies a ``>=A.B,<C.D`` range (lower-inclusive, upper-exclusive).

    Returns False for an unparseable ``version`` (untrusted, never a crash). Raises ValueError on a
    malformed ``range_str`` — that's a programming error in PEBRA, not user input.
    """
    rm = _RANGE.match(range_str)
    if not rm:
        raise ValueError(f"unsupported range format: {range_str!r}")
    lo = (int(rm.group(1)), int(rm.group(2)), 0)
    hi = (int(rm.group(3)), int(rm.group(4)), 0)
    v = _parse(version) if isinstance(version, str) else None
    if v is None:
        return False
    return lo <= v < hi
