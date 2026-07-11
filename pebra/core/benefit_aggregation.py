"""Pure action-level aggregation of measured per-file maintainability deltas."""

from __future__ import annotations

from collections.abc import Mapping


def aggregate_file_deltas(
    file_deltas: Mapping[str, tuple[float, float, float]],
) -> dict[str, float]:
    """Sum complexity and exposure-weight maintainability; return {} when nothing was measured."""
    if not file_deltas:
        return {}
    ordered = [file_deltas[path] for path in sorted(file_deltas)]
    complexity = sum(float(delta[0]) for delta in ordered)
    if len(ordered) == 1:
        return {
            "complexity_delta": complexity,
            "maintainability_index_delta": float(ordered[0][1]),
        }
    if all(float(delta[2]) <= 0.0 for delta in ordered):
        return {
            "complexity_delta": complexity,
            "maintainability_index_delta": sum(float(delta[1]) for delta in ordered) / len(ordered),
        }
    weighted_mi = 0.0
    for path in sorted(file_deltas):
        cc_delta, mi_delta, raw_weight = file_deltas[path]
        weight = max(0.0, float(raw_weight))
        # A bounded multiplier makes heavily-complex files count more without allowing a newly added
        # positive improvement to dilute an existing positive improvement (the failure mode of a
        # weighted mean). The action-level benefit model applies its own [-1,1] normalization later.
        multiplier = 1.0 + weight / (weight + 10.0) if weight > 0.0 else 1.0
        weighted_mi += float(mi_delta) * multiplier
    return {
        "complexity_delta": complexity,
        "maintainability_index_delta": weighted_mi,
    }
