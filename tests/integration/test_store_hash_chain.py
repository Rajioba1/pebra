"""Architecture §10 — SQLite store hash-chain is tamper-evident (plan §5 Phase-0 test).

Insert two assessments -> validate_chain() passes; mutate a stored row -> validate_chain() fails.
"""

from __future__ import annotations

import datetime
import json
import sqlite3

from pebra.adapters.store import db as dbmod
from pebra.adapters.store.db import GENESIS, SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _result(
    rau: float,
    *,
    decision: Decision = Decision.PROCEED,
    assessed_commit: str | None = None,
) -> AssessmentResult:
    return AssessmentResult(
        recommended_decision=decision,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.SENSITIVE_CONTEXT,
        scores={"rau": rau, "expected_loss": 0.10},
        repo_id="repo_local_example",
        repo_root="/abs/path",
        assessed_commit=assessed_commit,
        model_guidance_packet={"guidance_packet_id": "gp_a1", "decision": "proceed"},
    )


def test_pending_review_assessments_are_scoped_to_repo_and_head(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    expected = store.persist_assessment(
        _result(0.1, decision=Decision.ASK_HUMAN, assessed_commit="head-1"),
        {"task": "current", "candidate_replay": {"status": "available"}},
    )
    store.persist_assessment(
        _result(0.1, decision=Decision.ASK_HUMAN, assessed_commit="old-head"),
        {"task": "old", "candidate_replay": {"status": "available"}},
    )
    store.persist_assessment(
        _result(0.1, decision=Decision.PROCEED, assessed_commit="head-1"),
        {"task": "allowed", "candidate_replay": {"status": "available"}},
    )

    rows = store.pending_review_assessments("repo_local_example", "head-1")

    assert [row["assessment_id"] for row in rows] == [expected]
    assert rows[0]["request"]["task"] == "current"


def test_two_inserts_validate(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    id1 = store.persist_assessment(_result(0.31), {"task": "t1", "action_id": "a1"})
    id2 = store.persist_assessment(_result(0.22), {"task": "t2", "action_id": "a2"})
    assert id1 != id2
    assert store.validate_chain() is True


def test_new_assessment_persists_hash_covered_assessed_at(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)

    store.persist_assessment(_result(0.31), {"task": "timestamped"})

    content_json = store._con.execute(
        "SELECT content_json FROM assessments WHERE id = 1"
    ).fetchone()[0]
    content = json.loads(content_json)
    assert isinstance(content["assessed_at"], str)
    assert content["assessed_at"]
    assert store.validate_chain() is True


def test_tampering_with_assessed_at_breaks_assessment_chain(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(_result(0.31), {"task": "timestamped"})
    content_json = store._con.execute(
        "SELECT content_json FROM assessments WHERE id = 1"
    ).fetchone()[0]
    content = json.loads(content_json)
    content["assessed_at"] = "2026-01-01T00:00:00+00:00"
    store._con.execute(
        "UPDATE assessments SET content_json = ? WHERE id = 1",
        (json.dumps(content, sort_keys=True),),
    )
    store._con.commit()

    assert store.validate_chain() is False


def test_legacy_row_without_assessed_at_still_validates(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    result = _result(0.31)
    result.model_guidance_packet = None
    content_json = dbmod._canonical(result, {"task": "legacy"})
    row_hash = dbmod._row_hash(GENESIS, content_json)
    store._con.execute(
        "INSERT INTO assessments (repo_id, decision, content_json, prev_hash, row_hash) "
        "VALUES (?, ?, ?, ?, ?)",
        (result.repo_id, result.recommended_decision.value, content_json, GENESIS, row_hash),
    )
    store._con.commit()

    assert "assessed_at" not in json.loads(content_json)
    assert store.validate_chain() is True


def test_assessed_at_is_utc_iso_8601(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    store.persist_assessment(_result(0.31), {"task": "timestamped"})
    content = json.loads(
        store._con.execute("SELECT content_json FROM assessments WHERE id = 1").fetchone()[0]
    )

    parsed = datetime.datetime.fromisoformat(content["assessed_at"])

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == datetime.timedelta(0)
    assert content["assessed_at"].endswith("+00:00")


def test_assessment_and_predictions_share_one_recorded_at(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    store.persist_assessment(
        _result(0.31),
        {"task": "timestamped"},
        predictions=[
            {
                "target_type": "risk_binary",
                "target_name": "p_success",
                "predicted_value": 0.74,
                "action_id": "a1",
                "prediction_scope": "shadow",
                "provenance": {},
            }
        ],
    )
    assessed_at = json.loads(
        store._con.execute("SELECT content_json FROM assessments WHERE id = 1").fetchone()[0]
    )["assessed_at"]
    prediction_recorded_at = store._con.execute(
        "SELECT recorded_at FROM assessment_predictions WHERE assessment_id = 1"
    ).fetchone()[0]

    assert assessed_at == prediction_recorded_at


def test_malformed_legacy_assessed_at_remains_chain_valid(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    result = _result(0.31)
    result.model_guidance_packet = None
    content_json = dbmod._canonical(
        result,
        {"task": "legacy"},
        assessed_at="not-a-timestamp",
    )
    row_hash = dbmod._row_hash(GENESIS, content_json)
    store._con.execute(
        "INSERT INTO assessments (repo_id, decision, content_json, prev_hash, row_hash) "
        "VALUES (?, ?, ?, ?, ?)",
        (result.repo_id, result.recommended_decision.value, content_json, GENESIS, row_hash),
    )
    store._con.commit()

    assert store.validate_chain() is True
    assert store.list_assessments(result.repo_id)[0]["assessed_at"] is None


def test_guidance_packet_is_persisted(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    store.persist_assessment(_result(0.31), {"task": "t1", "action_id": "a1"})
    con = sqlite3.connect(str(tmp_path / "pebra.db"))
    count = con.execute("SELECT COUNT(*) FROM model_guidance_packets").fetchone()[0]
    con.close()
    assert count == 1


def test_tampering_breaks_the_chain(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(_result(0.31), {"task": "t1", "action_id": "a1"})
    store.persist_assessment(_result(0.22), {"task": "t2", "action_id": "a2"})
    assert store.validate_chain() is True

    # mutate the content of the first row without updating its hash
    con = sqlite3.connect(db)
    con.execute("UPDATE assessments SET content_json = '{\"tampered\": true}' WHERE id = 1")
    con.commit()
    con.close()

    assert SqliteStore(db).validate_chain() is False


def test_duplicate_repo_tampering_cannot_change_summary_scope(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(
        _result(0.31, decision=Decision.ASK_HUMAN), {"task": "t1", "action_id": "a1"}
    )
    store.close()

    con = sqlite3.connect(db)
    con.execute("UPDATE assessments SET repo_id = 'other' WHERE id = 1")
    con.commit()
    con.close()

    reopened = SqliteStore(db, read_only=True)
    assert reopened.list_assessments("other") == []
    assert list(reopened.assessment_facets("other")) == []
    assert list(reopened.assessment_facets("repo_local_example")) == [
        {"decision": "ask_human", "terminal_status": None}
    ]
    assert reopened.validate_chain() is False
    reopened.close()


def test_duplicate_decision_tampering_cannot_change_summary_decision(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(
        _result(0.31, decision=Decision.ASK_HUMAN), {"task": "t1", "action_id": "a1"}
    )
    store.close()

    con = sqlite3.connect(db)
    con.execute("UPDATE assessments SET decision = 'proceed' WHERE id = 1")
    con.commit()
    con.close()

    reopened = SqliteStore(db, read_only=True)
    original = reopened.list_assessments("repo_local_example")
    assert original[0]["decision"] == "ask_human"
    assert reopened.validate_chain() is False
    reopened.close()


def test_list_assessments_does_not_require_sqlite_json_functions(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(_result(0.31), {"task": "t1", "action_id": "a1"})

    real_connection = store._con

    class _NoJsonSqlConnection:
        def execute(self, sql, parameters=()):
            lowered = sql.lower()
            if "json_" in lowered or "->" in lowered:
                raise sqlite3.OperationalError("SQLite JSON functions are unavailable")
            return real_connection.execute(sql, parameters)

        def close(self):
            real_connection.close()

    store._con = _NoJsonSqlConnection()
    rows = store.list_assessments("repo_local_example")
    facets = list(store.assessment_facets("repo_local_example"))

    assert len(rows) == 1
    assert rows[0]["assessment_id"] == "asm_1"
    assert facets == [{"decision": "proceed", "terminal_status": None}]
    store.close()


def test_assessment_facets_are_newest_first(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    store.persist_assessment(_result(0.31, decision=Decision.PROCEED), {"task": "older"})
    store.persist_assessment(_result(0.22, decision=Decision.REJECT), {"task": "newer"})

    facets = list(store.assessment_facets("repo_local_example"))

    assert [facet["decision"] for facet in facets] == ["reject", "proceed"]
    store.close()


def test_tampering_with_guidance_packet_breaks_chain(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(_result(0.31), {"task": "t1", "action_id": "a1"})
    assert store.validate_chain() is True
    store.close()

    con = sqlite3.connect(db)
    con.execute("UPDATE model_guidance_packets SET packet_json = '{\"tampered\": true}' WHERE id = 1")
    con.commit()
    con.close()

    reopened = SqliteStore(db)
    assert reopened.validate_chain() is False
    reopened.close()


def test_tampering_with_sanction_event_breaks_chain(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    sid = store.create_sanction("repo_x", {"risk_profile": "rp_1", "valid": True})
    assert sid == "sx_1"
    assert store.validate_chain() is True
    store.close()

    con = sqlite3.connect(db)
    con.execute("UPDATE sanction_events SET sanction_json = '{\"tampered\": true}' WHERE id = 1")
    con.commit()
    con.close()

    reopened = SqliteStore(db)
    assert reopened.validate_chain() is False
    reopened.close()
