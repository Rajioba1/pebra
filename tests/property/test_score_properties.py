"""Architecture §5 — property-based invariants (plan §5 Phase-0 property tests).

- probability inputs in [0,1] keep scores well-formed
- edit_confidence stays in (0,1]
- benefit monotonicity: worse maintainability delta never raises benefit
- utility_sd is non-negative and monotonic in variance (cannot narrow below the evidence floor)
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from pebra.core import benefit_model as bm
from pebra.core import score_math as sm

unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
pos_unit = st.floats(min_value=1e-6, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(p=unit, d=unit)
def test_expected_loss_is_bounded(p, d) -> None:
    total, _ = sm.expected_loss([{"event": "test_regression", "p_event": p, "disutility": d}])
    assert 0.0 <= total <= 1.0


@given(
    f1=pos_unit, f2=pos_unit, f3=pos_unit, f4=pos_unit, f5=pos_unit, f6=pos_unit
)
def test_edit_confidence_stays_in_unit_interval(f1, f2, f3, f4, f5, f6) -> None:
    factors = {
        "p_success": f1, "evidence_quality": f2, "testability": f3,
        "reversibility": f4, "source_reliability": f5, "scope_control": f6,
    }
    c = sm.edit_confidence(factors)
    assert 0.0 < c <= 1.0


@given(better=st.floats(-1.0, 1.0), delta=st.floats(0.0, 1.0))
def test_benefit_monotonic_in_coupling(better, delta) -> None:
    # coupling is lower-is-better: a higher coupling_delta is worse and must not raise benefit.
    worse = better + delta
    b_better = bm.resolve_benefit(0.5, {"coupling_delta": better}, "measured", 1.0).benefit
    b_worse = bm.resolve_benefit(0.5, {"coupling_delta": worse}, "measured", 1.0).benefit
    assert b_worse <= b_better + 1e-12


@given(
    terms=st.lists(st.floats(0.0, 1.0, allow_nan=False), min_size=1, max_size=6),
    extra=st.floats(0.0, 1.0, allow_nan=False),
)
def test_utility_sd_nonneg_and_monotonic_in_variance(terms, extra) -> None:
    sd = sm.utility_sd(terms)
    sd_more = sm.utility_sd([*terms, extra])
    assert sd >= 0.0
    assert sd_more >= sd - 1e-12  # adding variance never narrows below the floor
    assert math.isclose(sd, math.sqrt(sum(terms)), rel_tol=1e-9, abs_tol=1e-12)
