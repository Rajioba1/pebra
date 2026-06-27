"""structural_feature_adapter (Phase-4 reframe / M5-prep) — PEBRA-owned structural feature capture.

Reframes the skipped "Phase 4 external tool adapters": instead of depending on an external
codeindex/sem binary (and NEVER letting an external verdict promote risk), PEBRA derives its own
structural context from signals it already computes (architecture map + import graph + symbol diff)
plus a stdlib-AST public-API probe. The payload is persisted with the prediction manifest so M5 can
learn facts scoped to real structural context.

Layer: adapter — may do I/O (reads ``__init__.py`` to detect public-API surface). Dep-light: stdlib
only (ast, pathlib). Implements ``StructuralFeatureProvider``. It does NOT feed scoring (the
controller attaches the payload to AssessmentInput for capture only; the engine ignores it).

HONESTY (v2, M5c.5): TWO fan-in signals are now emitted. ``container_file_fan_in_percentile`` is still
the import graph's repo-relative FILE fan-in. ``symbol_fan_in_percentile`` is the REAL per-symbol
call-graph fan-in from the graph engine (codegraph), taken from the assess-patched SymbolDiffEvidence —
0.0 when the engine is absent or the symbol wasn't trusted-resolved (an honest "no trusted value", with
the trust context carried in provenance's ``fanin_resolution_method`` / ``fanin_graph_freshness``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from pebra.core import structural_features
from pebra.core.models import AssessmentInput

# change kinds that represent a signature/contract change vs a body change
_SIGNATURE_KINDS = {"CONTRACT", "SIGNATURE"}
_BODY_KINDS = {"BEHAVIORAL", "SIDE_EFFECT"}


class StructuralFeatureAdapter:
    """Builds the structural feature payload from an already-assembled AssessmentInput."""

    def build_features(self, inp: AssessmentInput) -> dict[str, Any]:
        arch = inp.architecture_evidence
        sde = inp.symbol_diff_evidence
        expected_files = list(inp.action.expected_files)
        changed_symbols = list(sde.changed_symbols)

        # The feature payload is ACTION-scoped (one payload per action, attached to every prediction
        # target). symbol_id is the representative (first) changed symbol; multi-symbol edits share the
        # one payload. Per-symbol payloads would require per-symbol calibration targets (future slice).
        symbol_id = changed_symbols[0] if changed_symbols else (expected_files[0] if expected_files else "")
        file_path = symbol_id.split("::", 1)[0] if "::" in symbol_id else (
            expected_files[0] if expected_files else symbol_id
        )
        symbol_short = symbol_id.split("::", 1)[1].split(".")[-1] if "::" in symbol_id else ""

        change_kind = sde.max_change_kind
        visibility = sde.visibility
        is_public_api = visibility in {"public_api", "exported"} or self._detect_public_api(
            file_path, symbol_short, inp.repo_root
        )
        anchors = set(arch.matched_anchors)
        is_anchor = bool(anchors) and (
            file_path in anchors or any(f in anchors for f in expected_files)
        )

        provenance = {
            "schema_version": structural_features.SCHEMA_VERSION,
            "symbol_source": "symbol_diff",
            "structural_source": "architecture_map",
            "public_api_source": "structural_feature_adapter",
            # honest provenance for the proxied signals (file-level, not per-symbol)
            "domain_entrypoint_source": "architecture_map_file_level",
            "dependency_boundary_source": "bridge_centrality_proxy",
            "graph_freshness": arch.graph_freshness.value,
        }

        # M5c.5: stamp codegraph provenance when per-symbol fan-in evidence is present. The version +
        # index_version belong in calibration scope (a codegraph upgrade can move fan-in, so a
        # learned fact must know which engine produced its features); resolution_method/freshness make
        # an untrusted (name_fallback/stale) reading auditable rather than silently trusted.
        cg = getattr(inp, "fanin_evidence", None)
        if cg is not None:
            provenance["provider_version"] = cg.provider_version
            provenance["index_version"] = cg.index_version
            provenance["fanin_graph_freshness"] = cg.graph_freshness
            provenance["fanin_resolution_method"] = cg.resolution_method

        return structural_features.build_structural_features(
            symbol_id=symbol_id,
            file_path=file_path,
            action_type=inp.action.action_type,
            change_kind=change_kind,
            visibility=visibility,
            is_public_api=is_public_api,
            body_changed=change_kind in _BODY_KINDS,
            signature_changed=change_kind in _SIGNATURE_KINDS,
            container_file_fan_in_percentile=arch.god_node_score,
            # A0 (M5c.5): real per-symbol fan-in + the consequence verdict, patched onto SymbolDiffEvidence
            # on the assess path (codegraph-backed when trusted, 0.0/False otherwise). Captured so M5 can
            # learn/scope facts against true per-symbol fan-in context, not just file-level god-node score.
            symbol_fan_in_percentile=sde.symbol_fan_in_percentile,
            consequential_symbol_changed=sde.consequential_symbol_changed,
            bridge_centrality=arch.bridge_centrality,
            cycle_participation=arch.cycle_participation,
            is_architecture_anchor=is_anchor,
            domain_entrypoint=arch.domain_entrypoint,
            fan_out=arch.fan_out,
            # a file with cross-domain edges sits on a dependency boundary (proxy via bridge_centrality)
            dependency_boundary=arch.bridge_centrality > 0.0,
            matched_domains=arch.matched_domains,
            domain_criticality_hint=arch.domain_criticality_hint,
            criticality_stage=inp.criticality_stage,
            provenance=provenance,
        )

    @staticmethod
    def _detect_public_api(file_path: str, symbol_short: str, repo_root: str) -> bool:
        """PEBRA-owned public-API probe (stdlib AST). True if the symbol is part of a package's public
        surface: defined in an ``__init__.py`` (non-underscore) or listed in its package ``__all__``.
        Graceful: any missing file / dynamic ``__all__`` / parse error -> False."""
        if not file_path or not repo_root:
            return False
        try:
            target = Path(repo_root) / file_path
            if target.name == "__init__.py":
                if symbol_short and not symbol_short.startswith("_"):
                    return True
            init_py = target.parent / "__init__.py"
            if not init_py.is_file():
                return False
            tree = ast.parse(init_py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
                ):
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        names = {
                            el.value
                            for el in node.value.elts
                            if isinstance(el, ast.Constant) and isinstance(el.value, str)
                        }
                        if symbol_short in names:
                            return True
            return False
        except (OSError, SyntaxError, ValueError):
            return False
