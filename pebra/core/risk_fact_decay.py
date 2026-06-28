"""risk_fact_decay (AD-17 / §12.7) — pure, stdlib ``math`` only.

A learned fact loses influence as the scope it was learned on keeps changing. Decay is driven by
scope CHURN (the number of scope-relevant changes since the fact was learned), NOT wall-clock time —
a fact about code that never changes stays trustworthy; a fact about churning code fades.

    effective_weight = base_weight * exp(-scope_change_count / decay_strength)

The weight is not floored upward: a weak or stale fact must not become stronger because of a
threshold. ``MIN_EFFECTIVE_WEIGHT`` is the auto-apply eligibility threshold used by
``should_auto_apply``.

Constants are the spec defaults (``Report`` config §, learning.fact_decay): ``default_decay_strength``
and ``min_effective_weight``. ``scope_change_count`` is supplied by the read side (it is repo state,
not core math); this module only turns numbers into a weight, deterministically.
"""

from __future__ import annotations

import math

# learning.fact_decay defaults (spec config, AD-17). decay_strength is in units of "scope changes":
# at scope_change_count == decay_strength the weight is multiplied by exp(-1) ~= 0.368.
DEFAULT_DECAY_STRENGTH: float = 20.0
MIN_EFFECTIVE_WEIGHT: float = 0.10


def effective_weight(
    base_weight: float,
    scope_change_count: int,
    decay_strength: float = DEFAULT_DECAY_STRENGTH,
) -> float:
    """Churn-decayed fact weight.

    ``scope_change_count`` must be >= 0 (a count of changes); ``decay_strength`` must be > 0.
    With ``scope_change_count == 0`` the result is ``base_weight`` (no decay).
    """
    if base_weight < 0.0:
        raise ValueError(f"base_weight must be >= 0, got {base_weight}")
    if scope_change_count < 0:
        raise ValueError(f"scope_change_count must be >= 0, got {scope_change_count}")
    if decay_strength <= 0.0:
        raise ValueError(f"decay_strength must be > 0, got {decay_strength}")
    return base_weight * math.exp(-scope_change_count / decay_strength)


def should_auto_apply(
    weight: float,
    min_effective_weight: float = MIN_EFFECTIVE_WEIGHT,
) -> bool:
    """Whether a decayed fact still has enough influence to be auto-applied."""
    if weight < 0.0:
        raise ValueError(f"weight must be >= 0, got {weight}")
    if min_effective_weight < 0.0:
        raise ValueError(f"min_effective_weight must be >= 0, got {min_effective_weight}")
    return weight >= min_effective_weight
