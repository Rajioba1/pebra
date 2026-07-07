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
