"""ChangeVerifier port (Architecture §9). Protocol contract only.

Returns a summary of the *actual* post-edit diff (current HEAD, changed files, dependency/schema/
migration flags, and the reclassified actual change kind). I/O lives in the adapter.
"""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import ActualDiffSummary


class ChangeVerifier(Protocol):
    def actual_diff(self, repo_root: str, scope: str) -> ActualDiffSummary: ...
