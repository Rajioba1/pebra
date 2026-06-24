"""WeightResolver (Architecture §5) — pure, stdlib only.

Receives configured/elicited/cold-start criterion weights and returns normalized weights with
provenance. It does not read config files (adapters load config and pass values in). It rejects
negative weights, falls back to documented equal-weight cold-start when weights are missing,
normalizes to sum 1, and reports consistency warnings without mutating the assessment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_EPS = 1e-9


@dataclass(frozen=True)
class ResolvedWeights:
    weights: dict[str, float]
    source: str  # cold_start | provided
    warnings: list[str] = field(default_factory=list)


def resolve_weights(
    weights: dict[str, float] | None, criteria: list[str]
) -> ResolvedWeights:
    if not criteria:
        raise ValueError("at least one criterion is required")

    if weights is None:
        equal = 1.0 / len(criteria)
        return ResolvedWeights(weights={c: equal for c in criteria}, source="cold_start")

    warnings: list[str] = []
    for name, value in weights.items():
        if value < 0:
            raise ValueError(f"negative weight for {name!r}: {value}")

    # default any missing criterion to 0 before normalization, and warn
    resolved = {c: float(weights.get(c, 0.0)) for c in criteria}
    missing = [c for c in criteria if c not in weights]
    if missing:
        warnings.append(f"missing weights defaulted to 0 before normalization: {missing}")

    total = sum(resolved.values())
    if total <= _EPS:
        raise ValueError("weights sum to zero; cannot normalize")
    if abs(total - 1.0) > 1e-6:
        warnings.append(f"weights summed to {total:.6g}; normalized to 1.0")

    normalized = {c: v / total for c, v in resolved.items()}
    return ResolvedWeights(weights=normalized, source="provided", warnings=warnings)
