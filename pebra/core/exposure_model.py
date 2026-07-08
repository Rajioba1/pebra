"""exposure_model — derive ``future_change_exposure`` (a benefit VALUE weight) from measured graph fan-in.

``future_change_exposure`` weights how much a measured maintainability delta MATTERS: a cleanliness
improvement is worth crediting in proportion to how exposed the code is to future change (a hot, high
fan-in hub matters; a dead leaf doesn't). RCA measures the delta precisely; THIS derives the weight from
the same trusted graph reach the MODIFY-risk term already uses — so it's exactly as trustworthy as that
injection, and never a guess: absent/untrusted graph -> 0.0.

Pure, stdlib only (core). BENEFIT-only: the caller only ever applies this to the benefit term, never to
risk/loss/gates.
"""

from __future__ import annotations

from pebra.core.graph_trust import effective_impact_percentile, is_trusted_fanin
from pebra.core.models import FanInEvidence


def derive_exposure(fanin: FanInEvidence | None, *, cap: float = 1.0) -> float:
    """[0,1] future-change exposure from a TRUSTED fan-in's graph reach; 0.0 when absent/untrusted.

    ``cap`` is a repo-configurable ceiling (default 1.0 = no cap), defensively re-clamped to [0,1] so a
    misconfigured cap > 1 can't leak a >1 weight into the benefit multiplier.
    """
    if not is_trusted_fanin(fanin):
        return 0.0
    bound = max(0.0, min(1.0, cap))
    return max(0.0, min(bound, effective_impact_percentile(fanin)))
