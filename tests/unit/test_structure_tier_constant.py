"""The uncertain-structure-tier set is a single named constant, not copy-pasted literals.

Cleanup #3: `decision_engine`, `modify_risk_model`, and `model_guidance` all fold
`codegraph_structural`/`codegraph_semantic` into UNKNOWN's escalation. That set now lives in ONE place
so a future tier rename/addition can't desync the gates. This pins the canonical value; the behavioural
gate coverage lives in the per-module decision/guidance/modify tests.
"""

from __future__ import annotations

from pebra.core.constants import UNCERTAIN_STRUCTURE_TIERS


def test_uncertain_structure_tiers_is_the_canonical_set():
    assert UNCERTAIN_STRUCTURE_TIERS == frozenset({"codegraph_structural", "codegraph_semantic"})
