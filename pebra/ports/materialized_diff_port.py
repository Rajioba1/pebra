"""MaterializedGraphDiffProvider port — dark-gated before/after graph comparison contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pebra.core.models import MaterializedGraphDiffResult


class MaterializedGraphDiffProvider(Protocol):
    def diff(
        self,
        *,
        before_files: Mapping[str, str | None],
        after_files: Mapping[str, str | None],
        repo_root: str,
    ) -> MaterializedGraphDiffResult: ...

    def diff_for_patch(
        self, *, repo_root: str, patch: str
    ) -> MaterializedGraphDiffResult:
        """Assess-path entrypoint: read the working-tree before-content of the patch's touched files,
        materialize the after, and diff. Fail-closed to an unavailable result."""
        ...
