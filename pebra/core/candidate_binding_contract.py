"""Candidate identity values shared across trust-boundary layers."""

from collections.abc import Mapping
import re
from typing import Final

CANDIDATE_BINDING_ALGORITHM: Final[str] = "sha256-normalized-content-v1"
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


def candidate_binding_is_valid(candidate: object) -> bool:
    """Return whether *candidate* is an exact normalized-content binding."""
    if not isinstance(candidate, Mapping):
        return False
    if candidate.get("algorithm") != CANDIDATE_BINDING_ALGORITHM:
        return False
    files = candidate.get("files")
    if not isinstance(files, Mapping) or not files:
        return False
    return all(
        isinstance(path, str)
        and bool(path)
        and isinstance(digest, str)
        and _DIGEST_RE.fullmatch(digest) is not None
        for path, digest in files.items()
    )
