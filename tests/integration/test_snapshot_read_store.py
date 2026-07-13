"""M5c — snapshot read adapter: active learned facts -> SnapshotBundle, with the read-port gates
(status='active', ratified, min-sample, calibration_method, well-formed value)."""

from __future__ import annotations

import json

from pebra.adapters.snapshot_read_store import SnapshotReadStore
from pebra.adapters.store.db import (
    GENESIS,
    SqliteStore,
    _risk_snapshot_canonical,
    _row_hash,
)


def _store(tmp_path) -> SqliteStore:
    return SqliteStore(str(tmp_path / "p.db"))


def _seed_snapshot(store, *, repo_id="r1", status="active", rs_id=None):
    prev_hash = store._con.execute(
        "SELECT row_hash FROM risk_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous = prev_hash[0] if prev_hash else GENESIS
    created_at = "2026-01-01T00:00:00Z"
    content = _risk_snapshot_canonical(repo_id, status, {}, created_at, {"hash_version": 2})
    row_hash = _row_hash(previous, content)
    cur = store._con.execute(
        "INSERT INTO risk_snapshots (repo_id, status, metrics_json, hash_version, created_at, "
        "prev_hash, row_hash) VALUES (?, ?, '{}', 2, ?, ?, ?)",
        (repo_id, status, created_at, previous, row_hash),
    )
    return cur.lastrowid


def _seed_fact(store, *, repo_id="r1", snapshot_id="1", target_name="p_success",
               target_type="risk_binary", scope_kind="global", scope_value="", rank=0,
               status="active", ratify=0, fact_type="learned_override", fact=None, fact_json=None, scope_json="{}",
               created_at="2026-01-01T00:00:00Z"):
    if fact_json is None:
        fact_json = json.dumps(fact if fact is not None
                                else {"value": 0.80, "sample_size": 100, "calibration_method": "brier_bucket"})
    prev_hash = store._con.execute(
        "SELECT row_hash FROM learned_risk_facts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous = prev_hash[0] if prev_hash else GENESIS
    content = json.dumps(
        {
            "repo_id": repo_id,
            "snapshot_id": snapshot_id,
            "fact_type": fact_type,
            "target_type": target_type,
            "target_name": target_name,
            "scope_kind": scope_kind,
            "scope_value": scope_value,
            "specificity_rank": rank,
            "scope": json.loads(scope_json),
            "fact": json.loads(fact_json) if fact_json.startswith("{") else fact_json,
            "status": status,
            "requires_human_ratification": ratify,
            "created_at": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    row_hash = _row_hash(previous, content)
    store._con.execute(
        "INSERT INTO learned_risk_facts (repo_id, snapshot_id, fact_type, target_type, target_name, "
        "scope_kind, scope_value, specificity_rank, scope_json, fact_json, status, "
        "requires_human_ratification, created_at, prev_hash, row_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (repo_id, snapshot_id, fact_type, target_type, target_name, scope_kind, scope_value, rank,
         scope_json, fact_json, status, ratify, created_at, previous, row_hash),
    )


def test_empty_db_returns_none(tmp_path) -> None:
    store = _store(tmp_path)
    assert SnapshotReadStore(store).load_active_snapshot("r1") is None
    store.close()


def test_read_path_validates_only_learning_chains() -> None:
    class FakeStore:
        def __init__(self) -> None:
            self.learning_checks = 0

        def validate_chain(self) -> bool:
            raise AssertionError("full ledger validation must not run on the hot read path")

        def validate_learning_chains(self) -> bool:
            self.learning_checks += 1
            return True

        def read_active_snapshot_rows(self, repo_id):
            assert repo_id == "r1"
            return None

    fake = FakeStore()
    assert SnapshotReadStore(fake).load_active_snapshot("r1") is None
    assert fake.learning_checks == 1


def test_shadow_snapshot_returns_none(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store, status="shadow")
    _seed_fact(store)
    assert SnapshotReadStore(store).load_active_snapshot("r1") is None
    store.close()


def test_active_snapshot_with_applicable_fact(tmp_path) -> None:
    store = _store(tmp_path)
    rs = _seed_snapshot(store)
    _seed_fact(store, snapshot_id=str(rs))
    bundle = SnapshotReadStore(store).load_active_snapshot("r1")
    store.close()
    assert bundle is not None and bundle.snapshot_id == f"rs_{rs}"
    (f,) = bundle.facts
    assert f.fact_id.startswith("lrf_") and f.value == 0.80
    assert f.sample_size == 100 and f.calibration_method == "brier_bucket"


def test_active_fact_hydrates_pooling_fields(tmp_path) -> None:
    store = _store(tmp_path)
    rs = _seed_snapshot(store)
    _seed_fact(
        store,
        snapshot_id=str(rs),
        fact={
            "value": 0.8,
            "sample_size": 100,
            "calibration_method": "brier_bucket",
            "weight": 0.75,
            "calibration_quality": 0.5,
            "scope_change_count": 7,
            "variance": 0.0015,
            "aleatoric_variance": 0.003,
        },
    )
    bundle = SnapshotReadStore(store).load_active_snapshot("r1")
    store.close()

    assert bundle is not None
    (f,) = bundle.facts
    assert f.weight == 0.75
    assert f.calibration_quality == 0.5
    assert f.scope_change_count == 7
    assert f.variance == 0.0015
    assert f.aleatoric_variance == 0.003


def test_negative_fact_variance_is_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(
        store,
        fact={
            "value": 0.8,
            "sample_size": 100,
            "calibration_method": "brier_bucket",
            "variance": -0.01,
        },
    )

    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_malformed_pooling_fields_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(
        store,
        fact={
            "value": 0.8,
            "sample_size": 100,
            "calibration_method": "brier_bucket",
            "weight": -1.0,
        },
    )
    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_rs_prefixed_snapshot_id_also_joins(tmp_path) -> None:
    # defensive: a fact stored with the "rs_{id}" display form (not bare "1") still joins
    store = _store(tmp_path)
    rs = _seed_snapshot(store)
    _seed_fact(store, snapshot_id=f"rs_{rs}")
    bundle = SnapshotReadStore(store).load_active_snapshot("r1")
    store.close()
    assert bundle is not None and len(bundle.facts) == 1


def test_low_sample_fact_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(store, fact={"value": 0.8, "sample_size": 5, "calibration_method": "brier_bucket"})
    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_missing_calibration_method_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(store, fact={"value": 0.8, "sample_size": 100, "calibration_method": ""})
    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_whitespace_calibration_method_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(store, fact={"value": 0.8, "sample_size": 100, "calibration_method": "   "})
    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_non_override_fact_type_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    rs = _seed_snapshot(store)
    _seed_fact(store, snapshot_id=str(rs), fact_type="diagnostic_note")
    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_tampered_learned_fact_chain_fails_closed(tmp_path) -> None:
    store = _store(tmp_path)
    rs = _seed_snapshot(store)
    _seed_fact(store, snapshot_id=str(rs), fact={"value": 0.8, "sample_size": 100, "calibration_method": "m"})
    store._con.execute(
        "UPDATE learned_risk_facts SET fact_json = ?",
        (json.dumps({"value": 0.1, "sample_size": 100, "calibration_method": "m"}),),
    )
    assert store.validate_chain() is False
    assert SnapshotReadStore(store).load_active_snapshot("r1") is None
    store.close()


def test_unratified_fact_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(store, ratify=1)
    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_shadow_fact_in_active_snapshot_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(store, status="shadow")
    assert SnapshotReadStore(store).load_active_snapshot("r1").facts == ()
    store.close()


def test_malformed_fact_json_invalidates_snapshot_read(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store)
    _seed_fact(store, fact_json="NOT JSON")
    _seed_fact(store, target_name="p_event.x")  # valid default fact
    bundle = SnapshotReadStore(store).load_active_snapshot("r1")
    store.close()
    assert bundle is None


def test_other_repo_excluded(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_snapshot(store, repo_id="r1")
    _seed_fact(store, repo_id="r1")
    assert SnapshotReadStore(store).load_active_snapshot("r2") is None
    store.close()


def test_newest_active_snapshot_wins(tmp_path) -> None:
    store = _store(tmp_path)
    old = _seed_snapshot(store)
    new = _seed_snapshot(store)
    _seed_fact(store, snapshot_id=str(old), target_name="p_success", fact={"value": 0.1, "sample_size": 100, "calibration_method": "m"})
    _seed_fact(store, snapshot_id=str(new), target_name="p_event.new", fact={"value": 0.2, "sample_size": 100, "calibration_method": "m"})
    bundle = SnapshotReadStore(store).load_active_snapshot("r1")
    store.close()
    assert bundle.snapshot_id == f"rs_{new}"
    assert [f.target_name for f in bundle.facts] == ["p_event.new"]
