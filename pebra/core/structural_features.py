"""structural_features (Phase-4 reframe / M5-prep) — pure: assemble the versioned structural feature
payload persisted with each prediction row.

PEBRA-owned. Pure stdlib + core only — the *I/O* parts (reading ``__init__.py`` for public-API,
deriving boundary edges from the import graph) live in ``adapters/structural_feature_adapter.py``,
which extracts the primitives and calls this assembler. Keeping the schema + versioning + field names
in core means M5's ``apply_snapshot`` can match learned-fact scopes against a stable contract.

HONESTY CONTRACT (v2, M5c.5): there are now TWO distinct fan-in signals.
  - ``container_file_fan_in_percentile`` — file-level import-graph fan-in (the v1 signal, always
    available from the architecture map).
  - ``symbol_fan_in_percentile`` — REAL per-symbol call-graph fan-in from the graph engine (codegraph),
    patched onto SymbolDiffEvidence on the assess path. It is 0.0 when the graph engine is absent or the
    symbol could not be trusted-resolved — that 0.0 is an HONEST "no trusted per-symbol fan-in", NOT a
    claim of low fan-in. The trust context (resolution method / freshness) is carried in ``provenance``
    (``fanin_resolution_method`` / ``fanin_graph_freshness``) so M5 can tell a trusted low fan-in from an
    absent graph. ``is_high_symbol_fan_in`` is True only on a trusted high value (>= ANCHOR_FANIN_PERCENTILE).
"""

from __future__ import annotations

from typing import Any

from pebra.core.constants import ANCHOR_FANIN_PERCENTILE

SCHEMA_VERSION = 2


def build_structural_features(
    *,
    symbol_id: str,
    file_path: str,
    action_type: str,
    change_kind: str,
    visibility: str,
    is_public_api: bool,
    body_changed: bool,
    signature_changed: bool,
    container_file_fan_in_percentile: float,
    bridge_centrality: float,
    cycle_participation: bool,
    is_architecture_anchor: bool,
    domain_entrypoint: bool,
    fan_out: int,
    dependency_boundary: bool,
    matched_domains: list[str],
    domain_criticality_hint: str | None,
    criticality_stage: str,
    symbol_fan_in_percentile: float,
    consequential_symbol_changed: bool,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the v2 feature payload from already-extracted primitives. No I/O."""
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": {
            "symbol_id": symbol_id,
            "file_path": file_path,
            "action_type": action_type,
            "change_kind": change_kind,
            "visibility": visibility,
            "is_public_api": is_public_api,
            # NOTE: no per-symbol `is_entrypoint` — entrypoint is only known at file level today and is
            # carried honestly as structural.domain_entrypoint (see honesty contract in docstring).
            "body_changed": body_changed,
            "signature_changed": signature_changed,
            # v2: whether this change was judged consequential (visibility/fan-in/side-effect), patched
            # from the assess-path symbol evidence so M5 can learn against the same consequence signal.
            "consequential_symbol_changed": consequential_symbol_changed,
        },
        "structural": {
            # container/file-level fan-in (v1): repo-relative file fan-in percentile (what
            # ArchitectureEvidence calls god_node_score); a "god node" is a high-fan-in container file.
            "container_file_fan_in_percentile": container_file_fan_in_percentile,
            "is_high_container_fan_in": container_file_fan_in_percentile >= ANCHOR_FANIN_PERCENTILE,
            # v2: REAL per-symbol call-graph fan-in (graph engine). 0.0 = no trusted value (see honesty
            # contract); trust context is in provenance.fanin_resolution_method / fanin_graph_freshness.
            "symbol_fan_in_percentile": symbol_fan_in_percentile,
            "is_high_symbol_fan_in": symbol_fan_in_percentile >= ANCHOR_FANIN_PERCENTILE,
            "bridge_centrality": bridge_centrality,
            "cycle_participation": cycle_participation,
            "is_architecture_anchor": is_architecture_anchor,
            "domain_entrypoint": domain_entrypoint,
            "fan_out": fan_out,
            "dependency_boundary": dependency_boundary,
        },
        "domain": {
            "matched_domains": list(matched_domains),
            "domain_criticality_hint": domain_criticality_hint,
            "criticality_stage": criticality_stage,
        },
        "provenance": dict(provenance),
    }
