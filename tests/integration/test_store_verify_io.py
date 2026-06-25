"""Architecture §9/§10 — store load_assessment + persist_guardrails (verify-side I/O)."""

from __future__ import annotations

import sqlite3

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _result() -> AssessmentResult:
    return AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.SENSITIVE_CONTEXT,
        scores={"symbol_scope_evidence": {"max_change_kind": "BEHAVIORAL"}, "rau": 0.31},
        repo_id="repo_local_example",
        repo_root="/abs/path",
        assessed_commit="abc123",
        model_guidance_packet={
            "guidance_packet_id": "gp_a1",
            "decision": "proceed",
            "binding": {
                "safe_scope": {"files": ["src/auth.py"]},
                "risky_scope": [],
                "required_checks_before_commit": ["pytest"],
            },
        },
    )


def test_load_assessment_returns_binding_and_assessed_commit(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    loaded = store.load_assessment(aid)
    assert loaded["assessed_commit"] == "abc123"
    assert loaded["model_guidance_packet"]["binding"]["safe_scope"]["files"] == ["src/auth.py"]
    assert loaded["scores"]["symbol_scope_evidence"]["max_change_kind"] == "BEHAVIORAL"
    assert loaded["repo_id"] == "repo_local_example"


def test_persist_guardrails_writes_row(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    gid = store.persist_guardrails(aid, {"pre_commit_decision": "proceed", "reasons": []})
    assert gid == "pag_1"
    con = sqlite3.connect(db)
    count = con.execute("SELECT COUNT(*) FROM post_assessment_guardrails").fetchone()[0]
    con.close()
    assert count == 1


def test_assessed_commit_in_chain_still_validates(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    assert store.validate_chain() is True


def test_persist_guardrails_serializes_real_verify_result(tmp_path) -> None:
    # guards the _result_to_dict -> json.dumps path: a real GuardrailResult (with a Decision enum)
    # must round-trip through persist_guardrails without a serialization error.
    from pebra.app.verify_controller import _result_to_dict
    from pebra.core import post_assessment_guardrails as pag
    from pebra.core.constants import Decision

    store = SqliteStore(str(tmp_path / "pebra.db"))
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    result = pag.evaluate(
        pag.GuardrailInput(
            assessed_commit="abc123", current_head="abc123", safe_scope_files=["src/auth.py"],
            changed_files=["src/auth.py"], dependency_changed=False, schema_changed=False,
            migration_changed=False, pre_edit_max_change_kind="BEHAVIORAL",
            actual_max_change_kind="BEHAVIORAL", actual_changed_symbols=[],
            contract_surface_changes=[], risky_scope=[], triggered_signals=set(),
            required_checks=[], completed_checks={},
        )
    )
    gid = store.persist_guardrails(aid, _result_to_dict(result))
    assert gid == "pag_1"
    assert result.pre_commit_decision is Decision.PROCEED


def test_guardrail_chain_validates_and_detects_tampering(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    store.persist_guardrails(aid, {"pre_commit_decision": "proceed", "reasons": []})
    store.persist_guardrails(aid, {"pre_commit_decision": "inspect_first", "reasons": ["x"]})
    assert store.validate_chain() is True

    con = sqlite3.connect(db)
    con.execute("UPDATE post_assessment_guardrails SET guardrails_json = '{\"x\": 1}' WHERE id = 1")
    con.commit()
    con.close()
    assert SqliteStore(db).validate_chain() is False


def test_guardrail_recorded_at_is_stored(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    store.persist_guardrails(aid, {"pre_commit_decision": "proceed", "reasons": []})
    con = sqlite3.connect(db)
    recorded_at = con.execute("SELECT recorded_at FROM post_assessment_guardrails").fetchone()[0]
    con.close()
    assert recorded_at and "T" in recorded_at  # ISO-8601 UTC


def test_tampering_recorded_at_breaks_guardrail_chain(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    store.persist_guardrails(aid, {"pre_commit_decision": "proceed", "reasons": []})
    assert store.validate_chain() is True
    con = sqlite3.connect(db)
    con.execute("UPDATE post_assessment_guardrails SET recorded_at = '2020-01-01T00:00:00+00:00'")
    con.commit()
    con.close()
    assert SqliteStore(db).validate_chain() is False


def test_tampering_assessment_id_breaks_guardrail_chain(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    store.persist_guardrails(aid, {"pre_commit_decision": "proceed", "reasons": []})
    con = sqlite3.connect(db)
    con.execute("UPDATE post_assessment_guardrails SET assessment_id = 999 WHERE id = 1")
    con.commit()
    con.close()
    assert SqliteStore(db).validate_chain() is False


def test_tampering_guardrail_prev_hash_breaks_chain(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    store.persist_guardrails(aid, {"pre_commit_decision": "proceed", "reasons": []})
    store.persist_guardrails(aid, {"pre_commit_decision": "inspect_first", "reasons": ["x"]})
    con = sqlite3.connect(db)
    con.execute("UPDATE post_assessment_guardrails SET prev_hash = 'forged' WHERE id = 2")
    con.commit()
    con.close()
    assert SqliteStore(db).validate_chain() is False


def test_row_id_rejects_malformed_assessment_id(tmp_path) -> None:
    import pytest
    store = SqliteStore(str(tmp_path / "pebra.db"))
    for bad in ("asm_", "asm_abc", "asm", "garbage"):
        with pytest.raises(KeyError):
            store.load_assessment(bad)


def test_sanction_lifecycle_active_lookup_and_invalidation(tmp_path) -> None:
    # AD-26: a sanction is bound to an assessment, looked up while active, and invalidated on drift.
    store = SqliteStore(str(tmp_path / "pebra.db"))
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    sid = store.create_sanction(
        "repo_local_example", {"assessment_id": aid, "risk_profile": "rp_1", "valid": True}
    )
    assert store.active_sanction_for_assessment(aid) is not None
    invalidated = store.invalidate_sanctions_for_assessment(aid, "scope drift")
    assert invalidated == [sid]
    assert store.active_sanction_for_assessment(aid) is None


def test_status_update_does_not_break_sanction_chain(tmp_path) -> None:
    # invalidation mutates the (un-hashed) status column; the integrity chain must stay valid.
    store = SqliteStore(str(tmp_path / "pebra.db"))
    aid = store.persist_assessment(_result(), {"task": "t", "action_id": "a1"})
    store.create_sanction("repo_local_example", {"assessment_id": aid, "risk_profile": "rp_1"})
    store.invalidate_sanctions_for_assessment(aid, "symbol drift")
    assert store.validate_chain() is True
