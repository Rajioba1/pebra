"""explanation_generator (Architecture §4/§8) — pure, stdlib only.

Turns the scored ``AssessmentResult`` into the human-readable *semantic* fields and Why lines for the
card. It returns bands and grounded sentences; the surface (CLI/MCP/dashboard) composes the layout.

Label rules (§4/§8): "RAU" is never printed — only **Value After Risk** as a band. **Risk Level** is
a band, not a float. **Affected Area** appears as a measured fact in Why, never as a verdict bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pebra.core.models import AssessmentResult

# criticality stage -> human Code Sensitivity label
_SENSITIVITY_LABEL = {"C0": "Low", "C1": "Low", "C2": "Moderate", "C3": "High", "C4": "Critical"}

# lightweight capability tokens for a Phase-0 sensitivity descriptor (architecture map enriches later)
_CAPABILITY_TOKENS = ("auth", "login", "payment", "billing", "session", "crypto", "migration")

_DEFAULT_RAU_BANDS = {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40}


@dataclass(frozen=True)
class Explanation:
    risk_level_band: str
    value_after_risk_band: str
    confidence_band: str
    confidence_percent: int
    code_sensitivity_label: str
    code_sensitivity_descriptor: str
    expected_damage: float
    risk_budget_percent: int
    affected_area: str
    why: list[str] = field(default_factory=list)


def risk_level_band(risk_budget_used: float) -> str:
    if risk_budget_used >= 1.0:
        return "Critical"
    if risk_budget_used >= 0.75:
        return "High"
    if risk_budget_used >= 0.25:
        return "Moderate"
    return "Low"


def value_after_risk_band(rau: float, bands: dict[str, float]) -> str:
    if rau < bands["reject_below"]:
        return "Negative"
    if rau < bands["borderline_below"]:
        return "Borderline"
    if rau < bands["strong_at"]:
        return "Positive"
    return "Strong"


def _capability_descriptor(symbol_scope_evidence: dict[str, Any]) -> str:
    blob = " ".join(symbol_scope_evidence.get("changed_symbols", [])).lower()
    for token in _CAPABILITY_TOKENS:
        if token in blob:
            return f"sensitive {token} code"
    return "sensitive code"


def _affected_area(symbol_scope_evidence: dict[str, Any]) -> str:
    n = len(symbol_scope_evidence.get("changed_symbols", []))
    if n == 0:
        return "unknown - no parsed symbols"
    # only claim "measured" when evidence is genuinely symbol-level; cold-start is "estimated"
    basis = "measured" if symbol_scope_evidence.get("scope_basis") == "symbol" else "estimated"
    return f"small - affects ~{n} symbol(s) / few call sites ({basis})"


def render(
    result: AssessmentResult, thresholds: dict[str, float] | None = None
) -> Explanation:
    s = result.scores
    bands = (thresholds or {}).get("rau_bands", _DEFAULT_RAU_BANDS)
    sse = result.symbol_scope_evidence

    risk_pct = round(s["risk_budget_used"] * 100)
    conf_pct = round(s["edit_confidence"] * 100)
    stage = s["criticality_stage"]
    expected_damage = round(s["expected_loss"], 2)
    var_band = value_after_risk_band(s["rau"], bands)
    sensitivity = _SENSITIVITY_LABEL.get(stage, "Moderate")
    descriptor = _capability_descriptor(sse)
    affected = _affected_area(sse)

    why: list[str] = [
        f"Risk budget {risk_pct}% used: expected_loss {expected_damage:.2f} divided by "
        f"{stage} threshold {s['effective_threshold']:.2f}.",
        f"Value After Risk is {var_band} after the uncertainty penalty.",
        f"Confidence is {conf_pct}% after repo evidence gathering.",
        f"Affected Area: {affected}.",
    ]
    if stage in {"C3", "C4"}:
        why.append(
            f"{stage} code is sensitive context, so confirmation is required."
        )

    return Explanation(
        risk_level_band=risk_level_band(s["risk_budget_used"]),
        value_after_risk_band=var_band,
        confidence_band=s["confidence_band"],
        confidence_percent=conf_pct,
        code_sensitivity_label=sensitivity,
        code_sensitivity_descriptor=descriptor,
        expected_damage=expected_damage,
        risk_budget_percent=risk_pct,
        affected_area=affected,
        why=why,
    )
