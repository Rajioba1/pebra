"""Phase-4 reframe — assessment_predictions features_json + hash_version v2.

Features are now part of the v2 prediction canonical, so tampering them breaks the chain. Legacy v1
rows (no features) still validate via the legacy canonical.
"""

from __future__ import annotations

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _result() -> AssessmentResult:
    return AssessmentResult(
        recommended_decision=Decision.PROCEED, requires_confirmation=False,
        action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
        scores={"benefit": 0.82}, repo_id="r", repo_root="/x",
    )


def _pred(features: dict) -> list[dict]:
    return [{
        "target_type": "risk_binary", "target_name": "p_success", "predicted_value": 0.74,
        "action_id": "a1", "prediction_scope": "shadow", "provenance": {}, "features": features,
    }]


def test_features_persisted_and_loaded(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    feats = {"schema_version": 1, "symbol": {"is_public_api": True}}
    asm = store.persist_assessment(_result(), {"task": "t"}, predictions=_pred(feats))
    rows = store.load_predictions(asm)
    store.close()
    assert rows[0]["features"] == feats


def test_features_in_chain_and_tamper_breaks_it(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(
        _result(), {"task": "t"}, predictions=_pred({"symbol": {"is_public_api": False}})
    )
    assert store.validate_chain() is True
    # flip a persisted feature directly -> must be detected (it's in the v2 canonical now)
    store._con.execute(
        "UPDATE assessment_predictions SET features_json = ? WHERE id = 1",
        ('{"symbol": {"is_public_api": true}}',),
    )
    assert store.validate_chain() is False
    store.close()


def test_action_id_and_repo_id_bound_into_v2_hash(tmp_path) -> None:
    # predictions are action-scoped; misattributing a row to another action/repo must be detected
    store = SqliteStore(str(tmp_path / "p.db"))
    store.persist_assessment(_result(), {"task": "t"}, predictions=_pred({"symbol": {}}))
    assert store.validate_chain() is True
    store._con.execute("UPDATE assessment_predictions SET action_id = 'a2' WHERE id = 1")
    assert store.validate_chain() is False
    store.close()


def test_repo_id_tamper_breaks_v2_chain(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    store.persist_assessment(_result(), {"task": "t"}, predictions=_pred({"symbol": {}}))
    store._con.execute("UPDATE assessment_predictions SET repo_id = 'evil' WHERE id = 1")
    assert store.validate_chain() is False
    store.close()


def test_empty_features_still_valid(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    store.persist_assessment(_result(), {"task": "t"}, predictions=_pred({}))
    assert store.validate_chain() is True
    store.close()


def test_legacy_v1_prediction_row_validates(tmp_path) -> None:
    # simulate a pre-Phase-4 row: hash computed by the legacy canonical, hash_version=1, no features
    from pebra.adapters.store import db as dbmod

    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(_result(), {"task": "t"})  # creates assessment row id=1
    recorded_at = "2026-06-26T00:00:00+00:00"
    legacy = dbmod._prediction_canonical_v1(1, "risk_binary", "p_success", 0.74, "shadow", {}, recorded_at)
    prev = store._last_prediction_hash()
    row_hash = dbmod._row_hash(prev, legacy)
    store._con.execute(
        "INSERT INTO assessment_predictions "
        "(assessment_id, repo_id, action_id, target_type, target_name, predicted_value, "
        " prediction_scope, label_status, shadow_mode, features_json, provenance_json, "
        " hash_version, recorded_at, prev_hash, row_hash) "
        "VALUES (1, 'r', 'a1', 'risk_binary', 'p_success', 0.74, 'shadow', 'pending', 1, '{}', '{}', "
        " 1, ?, ?, ?)",
        (recorded_at, prev, row_hash),
    )
    assert store.validate_chain() is True  # legacy row validated via the v1 canonical
    store.close()
    assert asm == "asm_1"
