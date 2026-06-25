"""contract_surface (Phase-1 ContractSurfaceProvider, Architecture §9).

Adapter: detects public-surface changes in the actual diff. Phase 1 ships the port + a conservative
adapter that reports no surface findings (full public-API / route / tool-schema / response-shape
detection — which needs before/after AST parsing — is a later enrichment). The guardrails already
enforce any findings this provider surfaces, so upgrading it later requires no core change.
"""

from __future__ import annotations

from pebra.core.models import ContractSurfaceFindings


class ContractSurfaceScanner:
    def contract_findings(
        self, repo_root: str, changed_files: list[str]
    ) -> ContractSurfaceFindings:
        # Phase-1 conservative default: no surface findings asserted without before/after parsing.
        return ContractSurfaceFindings(changes=[])
