"""Step 5 Phase 2 — the learned-fact + snapshot writer (M5d).

The highest-risk detail: the inserted learned-fact hash-canonical MUST byte-match the existing chain
validator (keys "scope"/"fact", not the column names) or the first promotion breaks validate_chain().
The golden test below re-derives the canonical the same way the validator does and asserts the hash.
"""

from __future__ import annotations

import json

from pebra.adapters.store.db import GENESIS, SqliteStore, _learned_fact_canonical, _row_hash


def _fact(**over):
    base = {
        "target_type": "risk_binary", "target_name": "p_success", "scope_kind": "global",
        "scope_value": "", "specificity_rank": 0, "scope_json": {},
        "fact_json": {
            "value": 0.42, "weight": 1.0, "sample_size": 150,
            "calibration_method": "observed_rate_v1", "calibration_quality": 1.0,
            "scope_change_count": 0, "provider_version": None, "index_version": None,
        },
        "fact_type": "learned_override", "status": "active", "requires_human_ratification": False,
    }
    base.update(over)
    return base


def _metrics():
    return {"promotion_reason": "M5d_auto_promotion", "hash_version": 2}


def test_chain_validates_after_first_insert(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    snap_id, fact_ids = store.insert_learned_fact_batch_with_snapshot("r", _metrics(), [_fact()])
    assert snap_id.startswith("rs_")
    assert len(fact_ids) == 1 and fact_ids[0].startswith("lrf_")
    assert store.validate_chain() is True
    assert store.validate_learning_chains() is True
    store.close()


def test_learned_fact_canonical_byte_matches_validator(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot("repo1", _metrics(), [_fact()])
    row = store._con.execute(
        "SELECT repo_id, snapshot_id, fact_type, target_type, target_name, scope_kind, "
        "scope_value, specificity_rank, scope_json, fact_json, status, "
        "requires_human_ratification, created_at, row_hash FROM learned_risk_facts"
    ).fetchone()
    (repo_id, snap_id, fact_type, tt, tn, sk, sv, rank, scope_json_text, fact_json_text,
     status, ratification, created_at, stored_hash) = row
    recomputed = _learned_fact_canonical(
        repo_id, snap_id, fact_type, tt, tn, sk, sv, rank,
        json.loads(scope_json_text), json.loads(fact_json_text), status, ratification, created_at,
    )
    assert _row_hash(GENESIS, recomputed) == stored_hash
    store.close()


def test_two_facts_sequential_chain(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    _, fact_ids = store.insert_learned_fact_batch_with_snapshot(
        "r", _metrics(), [_fact(scope_kind="global"), _fact(scope_kind="symbol", scope_value="m::f")]
    )
    assert len(fact_ids) == 2
    assert store.validate_chain() is True
    store.close()


def test_zero_facts_rejected_without_hiding_prior_snapshot(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot("r", _metrics(), [_fact()])
    before = store.read_active_snapshot_rows("r")
    assert before is not None and len(before["facts"]) == 1

    try:
        store.insert_learned_fact_batch_with_snapshot("r", _metrics(), [])
    except ValueError:
        pass
    else:
        raise AssertionError("empty promotion batch should be rejected")

    after = store.read_active_snapshot_rows("r")
    assert after == before
    assert store.validate_chain() is True
    store.close()


def test_requires_human_ratification_stored_as_int_and_candidate(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot(
        "r", _metrics(),
        [_fact(status="candidate", requires_human_ratification=True)],
        snapshot_status="active",
    )
    rh, status = store._con.execute(
        "SELECT requires_human_ratification, status FROM learned_risk_facts"
    ).fetchone()
    assert rh == 1 and status == "candidate"
    assert store.validate_chain() is True
    store.close()


def test_benefit_snapshot_is_read_alongside_active_risk_facts(tmp_path):
    # Critical regression: risk + benefit promotion each write their own ACTIVE snapshot. The assess
    # read path must return the newest active snapshot CONTAINING RISK facts — a later benefit-only
    # snapshot must not shadow the risk overrides (apply_snapshot only applies risk_binary facts).
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_auto_promotion", "hash_version": 2},
        [_fact(target_type="risk_binary", target_name="p_success")],
    )  # rs_1: risk
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_benefit_promotion", "hash_version": 2},
        [_fact(target_type="benefit_binary", target_name="immediate_benefit_realized")],
    )  # rs_2: benefit (newer)

    bundle = store.read_active_snapshot_rows("r")
    store.close()
    assert bundle is not None
    assert any(f["target_type"] == "benefit_binary" for f in bundle["facts"])
    assert any(f["target_name"] == "p_success" for f in bundle["facts"])
    assert any(f["target_name"] == "immediate_benefit_realized" for f in bundle["facts"])


def test_unusable_newer_risk_snapshot_does_not_mask_active_risk_facts(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_auto_promotion", "hash_version": 2},
        [_fact(target_type="risk_binary", target_name="p_success")],
    )  # rs_1: active/applicable risk fact
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_auto_promotion", "hash_version": 2},
        [_fact(target_type="risk_binary", target_name="p_event.x", status="candidate")],
    )  # rs_2: newer risk snapshot, but no applicable active fact

    bundle = store.read_active_snapshot_rows("r")
    store.close()
    assert bundle is not None
    assert bundle["snapshot_id"] == "rs_1"
    assert [f["target_name"] for f in bundle["facts"]] == ["p_success"]


def test_low_sample_newer_risk_snapshot_does_not_mask_active_risk_facts(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_auto_promotion", "hash_version": 2},
        [_fact(target_type="risk_binary", target_name="p_success")],
    )
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_auto_promotion", "hash_version": 2},
        [_fact(
            target_type="risk_binary",
            target_name="p_event.low_sample",
            fact_json={"value": 0.2, "weight": 1.0, "sample_size": 1,
                       "calibration_method": "observed_rate_v1"},
        )],
    )

    bundle = store.read_active_snapshot_rows("r")
    store.close()
    assert bundle is not None
    assert bundle["snapshot_id"] == "rs_1"
    assert [f["target_name"] for f in bundle["facts"]] == ["p_success"]


def test_active_facts_readable_via_read_path(tmp_path):
    # an active snapshot + active ratified-not-required fact must be visible to read_active_snapshot_rows.
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot("r", _metrics(), [_fact()], snapshot_status="active")
    bundle = store.read_active_snapshot_rows("r")
    store.close()
    assert bundle is not None
    assert any(f["target_name"] == "p_success" for f in bundle["facts"])
