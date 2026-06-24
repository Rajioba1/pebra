"""high_risk_controls (Architecture §5/§8, AD-26) — pure trigger -> control-blueprint selector.

Given the derived ``high_risk_triggers`` it selects the required pre-commit controls and the control
blueprint id(s). It does not decide; it equips the controlled-high-risk envelope the decision engine
and guidance packet reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# risk_class -> (control_blueprint_id, required_controls)
_BLUEPRINTS: dict[str, tuple[str, list[str]]] = {
    "payment_side_effect": (
        "payment_change",
        ["sandbox_payment_tests", "idempotency_evidence", "reconciliation_baseline"],
    ),
    "external_state_write": (
        "external_state_change",
        ["dry_run_preview", "rollback_plan", "reconciliation_baseline"],
    ),
    "migration_side_effect": (
        "schema_migration",
        ["reversible_migration_proof", "backup_evidence", "staging_apply"],
    ),
    "architecture_anchor_behavioral_change": (
        "broad_god_node_behavioral_edit",
        ["targeted_tests", "impact_preview"],
    ),
    "public_api_contract_change": (
        "api_contract_change",
        ["consumer_impact_review", "contract_tests"],
    ),
}

# Conservative default for an unrecognized but real trigger.
_DEFAULT_CONTROLS = ["human_review", "impact_preview"]


@dataclass(frozen=True)
class ControlSelection:
    required_controls: list[str] = field(default_factory=list)
    control_blueprint_ids: list[str] = field(default_factory=list)


def select_controls(high_risk_triggers: list[dict[str, Any]]) -> ControlSelection:
    controls: list[str] = []
    blueprints: list[str] = []
    for trigger in high_risk_triggers:
        risk_class = trigger.get("risk_class", "")
        blueprint = _BLUEPRINTS.get(risk_class)
        if blueprint is None:
            for c in _DEFAULT_CONTROLS:
                if c not in controls:
                    controls.append(c)
            continue
        bp_id, bp_controls = blueprint
        if bp_id not in blueprints:
            blueprints.append(bp_id)
        for c in bp_controls:
            if c not in controls:
                controls.append(c)
    return ControlSelection(required_controls=controls, control_blueprint_ids=blueprints)
