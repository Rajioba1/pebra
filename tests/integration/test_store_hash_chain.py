"""Architecture §10 — SQLite store hash-chain is tamper-evident (plan §5 Phase-0 test).

Insert two assessments -> validate_chain() passes; mutate a stored row -> validate_chain() fails.
"""

from __future__ import annotations

import sqlite3

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _result(rau: float) -> AssessmentResult:
    return AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.SENSITIVE_CONTEXT,
        scores={"rau": rau, "expected_loss": 0.10},
        repo_id="repo_local_example",
        repo_root="/abs/path",
        model_guidance_packet={"guidance_packet_id": "gp_a1", "decision": "proceed"},
    )


def test_two_inserts_validate(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    id1 = store.persist_assessment(_result(0.31), {"task": "t1", "action_id": "a1"})
    id2 = store.persist_assessment(_result(0.22), {"task": "t2", "action_id": "a2"})
    assert id1 != id2
    assert store.validate_chain() is True


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
