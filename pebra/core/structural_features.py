"""structural_features (Phase-4 reframe / M5-prep) — pure: assemble the versioned structural feature
payload persisted with each prediction row.

PEBRA-owned. Pure stdlib + core only — the *I/O* parts (reading ``__init__.py`` for public-API,
deriving boundary edges from the import graph) live in ``adapters/structural_feature_adapter.py``,
which extracts the primitives and calls this assembler. Keeping the schema + versioning + field names
in core means M5's ``apply_snapshot`` can match learned-fact scopes against a stable contract.

HONESTY CONTRACT (ratified): the v1 fan-in signal is CONTAINER-FILE level
(``container_file_fan_in_percentile``). The import graph has no per-symbol call graph yet, so there is
NO ``symbol_fan_in_percentile`` field — true per-symbol fan-in is a later precision slice, not a fake
zero. M5 must not pretend it has a function-level call graph.
"""

from __future__ import annotations

from typing import Any

from pebra.core.constants import ANCHOR_FANIN_PERCENTILE

SCHEMA_VERSION = 1


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
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the v1 feature payload from already-extracted primitives. No I/O."""
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
        },
        "structural": {
            # honest: container/file-level fan-in, NOT per-symbol (see module docstring). This IS the
            # repo-relative file fan-in percentile (what ArchitectureEvidence calls god_node_score);
            # a "god node" is just a high-fan-in container file -> the derived flag below.
            "container_file_fan_in_percentile": container_file_fan_in_percentile,
            "is_high_container_fan_in": container_file_fan_in_percentile >= ANCHOR_FANIN_PERCENTILE,
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
