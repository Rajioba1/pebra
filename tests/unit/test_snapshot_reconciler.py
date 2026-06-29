"""Phase 5 closure — snapshot_reconciler (drift-freeze).

Drift = mean abs difference between each ACTIVE fact's stored value and the freshly recomputed empirical
rate over the current calibration rows for the same scope. Promotion freezes when drift >= threshold,
so a learned snapshot that has diverged from the ledger can't keep being extended blindly.
"""

from __future__ import annotations

import json

import pytest

from pebra.core import snapshot_reconciler as sr


def _snap(facts):
    return {"snapshot_id": "rs_1", "facts": [
        {"target_name": tn, "scope_kind": sk, "scope_value": sv, "scope_json": "{}",
         "fact_json": json.dumps({"value": v})}
        for tn, sk, sv, v in facts
    ]}


def _crow(tn, y):
    return {"target_name": tn, "actual_outcome": y, "features": {}}


def test_drift_zero_when_no_active_snapshot():
    assert sr.compute_drift(None, [_crow("p_success", 1)]) == 0.0


def test_drift_zero_when_no_matching_rows():
    assert sr.compute_drift(_snap([("p_success", "global", "", 0.5)]), []) == 0.0


def test_drift_global_scope_abs_diff():
    snap = _snap([("p_success", "global", "", 0.9)])         # learned says 0.9
    rows = [_crow("p_success", 0) for _ in range(4)]         # ledger now says 0.0
    assert sr.compute_drift(snap, rows) == pytest.approx(0.9)


def test_drift_averages_over_multiple_facts():
    snap = _snap([("p_success", "global", "", 0.8), ("p_event.x", "global", "", 0.0)])
    rows = ([_crow("p_success", 1) for _ in range(2)]        # rate 1.0, |0.8-1.0|=0.2
            + [_crow("p_event.x", 1) for _ in range(2)])     # rate 1.0, |0.0-1.0|=1.0
    assert sr.compute_drift(snap, rows) == pytest.approx((0.2 + 1.0) / 2)


def test_malformed_fact_json_skipped():
    snap = {"snapshot_id": "rs_1", "facts": [
        {"target_name": "p_success", "scope_kind": "global", "scope_value": "",
         "scope_json": "{}", "fact_json": "{not-json"}
    ]}
    assert sr.compute_drift(snap, [_crow("p_success", 0)]) == 0.0


def test_should_freeze_threshold():
    assert sr.should_freeze(0.3, 0.2) is True
    assert sr.should_freeze(0.2, 0.2) is True
    assert sr.should_freeze(0.1, 0.2) is False
