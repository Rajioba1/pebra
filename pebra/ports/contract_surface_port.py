"""ContractSurfaceProvider port (Architecture §9). Protocol contract only.

Detects public-surface changes (public API, route handler, MCP/RPC tool schema, exported symbol,
response/consumer shape) in the actual diff. This is not a substitute for full symbol reclassification.
"""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import ContractSurfaceFindings


class ContractSurfaceProvider(Protocol):
    def contract_findings(
        self, repo_root: str, changed_files: list[str]
    ) -> ContractSurfaceFindings: ...
