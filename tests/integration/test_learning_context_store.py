from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pebra.adapters.store import db as dbmod
from pebra.adapters.store.db import SqliteStore
from pebra.app import record_outcome_controller
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult
from pebra.core.learning_context import RECALL_CANDIDATE_WINDOW, canonical_entry_content


def _assessment(
    store: SqliteStore,
    *,
    repo_id: str = "r1",
    task: str = "fix login",
    gates_fired: list[dict] | None = None,
) -> str:
    return store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={
                "expected_loss": 0.1,
                "benefit": 0.82,
                "expected_utility": 0.4,
                "utility_sd": 0.2,
                "rau": 0.31,
            },
            repo_id=repo_id,
            repo_root="/repo",
            assessed_commit="abc123",
            gates_fired=gates_fired or [],
        ),
        {
            "task": task,
            "action_id": "a1",
            "revision_envelope": {
                "expected_files": ["src/auth.py"],
                "public_symbols": ["auth.login"],
            },
        },
    )


def _verified_completed(store: SqliteStore, **kwargs) -> str:
    assessment_id = _assessment(store, **kwargs)
    store.persist_guardrails(
        assessment_id,
        {"pre_commit_decision": "proceed", "measured_benefit": 0.5},
    )
    store.record_outcome(assessment_id, "completed", {"lesson": "untrusted"})
    return assessment_id


def test_learning_context_is_repo_scoped_literal_and_tamper_evident(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task='fix "AND" [login]')
    entry = store.materialize_learning_context(assessment_id)
    assert entry is not None
    assert "untrusted" not in entry.lesson
    recall = store.recall_learning_context("r1", 'fix "AND" [login]')
    assert recall.entries and recall.entries[0].learning_context_id == entry.learning_context_id
    assert store.recall_learning_context("other", "login").entries == ()
    assert store.recall_learning_context("r1", "[]---").status == "empty"
    assert store.validate_chain()
    store._con.execute("UPDATE learning_context SET lesson = 'tampered' WHERE id = 1")
    assert not store.validate_chain()
    assert store.recall_learning_context("r1", "login").status == "corrupt"
    store.close()


def test_learning_context_source_hash_links_are_verified(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task="source link")
    assert store.materialize_learning_context(assessment_id) is not None
    store._con.execute(
        "UPDATE outcomes SET row_hash = ? WHERE assessment_id = 1", ("c" * 64,)
    )
    assert store.recall_learning_context("r1", "source").status == "corrupt"
    store.close()


def test_gate_identifiers_are_persisted_hashed_and_recalled(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(
        store,
        task="gated lesson",
        gates_fired=[
            {"name": "policy_boundary", "reason": "untrusted prose"},
            {"name": "evidence_validity"},
        ],
    )
    entry = store.materialize_learning_context(assessment_id)
    assert entry is not None
    assert entry.gates_fired == ("evidence_validity", "policy_boundary")
    assert "untrusted prose" not in entry.lesson
    assert store.recall_learning_context("r1", "gated").entries == (entry,)
    store._con.execute(
        "UPDATE learning_context SET gates_fired = ? WHERE id = 1",
        ('["different_gate"]',),
    )
    assert store.validate_chain() is False
    store.close()


def test_v1_learning_rows_remain_chain_valid_after_gate_column_migration(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task="legacy learning")
    entry = store.materialize_learning_context(assessment_id)
    assert entry is not None
    legacy_hash = hashlib.sha256(
        (
            dbmod.GENESIS
            + canonical_entry_content(entry, dbmod.GENESIS, hash_version=1)
        ).encode("utf-8")
    ).hexdigest()
    store._con.execute(
        "UPDATE learning_context SET gates_fired = '[]', hash_version = 1, row_hash = ? "
        "WHERE id = 1",
        (legacy_hash,),
    )
    assert store.validate_chain() is True
    recall = store.recall_learning_context("r1", "legacy")
    assert recall.status == "available"
    assert recall.entries[0].gates_fired == ()
    # Version 1 never hashed a gate field. Only the migration default [] is trustworthy;
    # otherwise an attacker could smuggle unverified gate names beside a still-valid v1 hash.
    store._con.execute(
        "UPDATE learning_context SET gates_fired = '[\"unhashed_gate\"]' WHERE id = 1"
    )
    assert store.validate_chain() is False
    assert store.recall_learning_context("r1", "legacy").status == "corrupt"
    store.close()


def test_recall_rejects_tampered_source_content_and_malformed_canonical_json(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task="tamper source")
    assert store.materialize_learning_context(assessment_id) is not None
    store._con.execute(
        "UPDATE assessments SET content_json = ? WHERE id = 1", ('{"repo_id":"r1"}',)
    )
    assert store.recall_learning_context("r1", "tamper").status == "corrupt"
    store._con.execute("UPDATE learning_context SET target_files = 'not-json' WHERE id = 1")
    assert store.validate_chain() is False
    store.close()


def test_materialization_is_idempotent_and_distinct_assessments_survive(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    first_id = _verified_completed(store, task="shared keyword")
    first = store.materialize_learning_context(first_id)
    assert store.materialize_learning_context(first_id) == first
    second_id = _verified_completed(store, task="shared keyword")
    second = store.materialize_learning_context(second_id)
    assert first is not None and second is not None
    recall = store.recall_learning_context("r1", "shared")
    assert [entry.assessment_id for entry in recall.entries] == [second_id, first_id]
    assert store.chain_status()["counts"]["learning_context"] == 2
    store.close()


def test_recall_has_five_row_and_byte_bounds_and_search_is_read_only(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    for index in range(7):
        assessment_id = _verified_completed(store, task=f"bounded common {index}")
        assert store.materialize_learning_context(assessment_id) is not None
    before = store._con.execute(
        "SELECT learning_context_id, row_hash FROM learning_context ORDER BY id"
    ).fetchall()
    recall = store.recall_learning_context("r1", "common", byte_limit=32768)
    assert len(recall.entries) == 5
    assert recall.truncated is True
    tiny = store.recall_learning_context("r1", "common", byte_limit=1)
    assert tiny.entries == () and tiny.truncated is True
    after = store._con.execute(
        "SELECT learning_context_id, row_hash FROM learning_context ORDER BY id"
    ).fetchall()
    assert after == before
    store.close()


def test_raw_completed_rows_never_become_lessons_even_with_proceed_guardrail(tmp_path: Path) -> None:
    db = tmp_path / "pebra.db"
    store = SqliteStore(str(db))
    without_guardrail = _assessment(store, task="raw absent")
    store.record_outcome(without_guardrail, "completed", {})
    with_guardrail = _assessment(store, task="raw present")
    store.persist_guardrails(with_guardrail, {"pre_commit_decision": "proceed"})
    store.record_outcome(with_guardrail, "completed", {})
    assert store.recall_learning_context("r1", "raw").entries == ()
    store.rebuild_learning_context_fts()
    assert store.recall_learning_context("r1", "raw").entries == ()
    store.close()
    reopened = SqliteStore(str(db))
    assert reopened.recall_learning_context("r1", "raw").entries == ()
    reopened.close()


def test_fts_rebuild_restores_only_the_rebuildable_index(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task="rebuild lexical")
    entry = store.materialize_learning_context(assessment_id)
    assert entry is not None
    store._con.execute("DELETE FROM learning_context_fts")
    assert store.recall_learning_context("r1", "lexical").entries == ()
    store.rebuild_learning_context_fts()
    assert store.recall_learning_context("r1", "lexical").entries == (entry,)
    assert store.validate_chain()
    store.close()

    readonly = SqliteStore(str(tmp_path / "pebra.db"), read_only=True)
    assert readonly.recall_learning_context("r1", "lexical").entries == (entry,)
    readonly.close()


def test_partial_materialization_failure_never_repairs_from_raw_outcome(tmp_path: Path) -> None:
    class _FailingMaterializer:
        def materialize_learning_context(self, _assessment_id: str):
            raise RuntimeError("simulated context failure")

    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _assessment(store, task="partial failure")
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})
    result = record_outcome_controller.record_outcome(
        assessment_id,
        "completed",
        outcome_port=store,
        learning_context_port=_FailingMaterializer(),
    )
    assert result.outcome_recorded is True
    assert result.context_materialized is False
    assert result.context_error == "RuntimeError"
    assert store.learning_context_for_assessment(assessment_id) is None
    with pytest.raises(ValueError, match="already has a terminal outcome"):
        record_outcome_controller.record_outcome(
            assessment_id,
            "completed",
            outcome_port=store,
            learning_context_port=store,
        )
    assert store.learning_context_for_assessment(assessment_id) is None
    store.close()


def test_materializer_independently_rereads_latest_guardrail(tmp_path: Path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _assessment(store, task="guardrail changed")
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})

    class _ChangedGuardrail:
        def materialize_learning_context(self, value: str):
            store.persist_guardrails(value, {"pre_commit_decision": "test_first"})
            return store.materialize_learning_context(value)

    result = record_outcome_controller.record_outcome(
        assessment_id,
        "completed",
        outcome_port=store,
        learning_context_port=_ChangedGuardrail(),
    )
    assert result.outcome_recorded is True
    assert result.context_materialized is False
    assert store.learning_context_for_assessment(assessment_id) is None
    store.close()


@pytest.mark.parametrize(
    "alias", ["alias_1", "asm_01", "asm_+1", "asm_ 1", "asm_-1", "asm_0"]
)
def test_learning_materializer_accepts_only_canonical_positive_assessment_ids(
    tmp_path: Path, alias: str
) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task="canonical id")
    assert assessment_id == "asm_1"
    with pytest.raises(KeyError, match="invalid assessment id"):
        store.materialize_learning_context(alias)
    assert store.chain_status()["counts"]["learning_context"] == 0
    assert store.materialize_learning_context(assessment_id) is not None
    assert store.chain_status()["counts"]["learning_context"] == 1
    store.close()


def test_duplicate_source_outcomes_fail_closed_if_database_constraint_is_missing(
    tmp_path: Path,
) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task="duplicate source")
    store._con.execute("DROP INDEX ux_outcomes_assessment")
    guidance, status, detail_json, previous_hash = store._con.execute(
        "SELECT guidance_packet_id, terminal_status, detail_json, row_hash "
        "FROM outcomes WHERE assessment_id = 1"
    ).fetchone()
    recorded_at = "2026-07-22T00:00:00+00:00"
    detail = json.loads(detail_json)
    content = dbmod._outcome_canonical(
        1, status, detail, recorded_at, guidance, 2
    )
    store._con.execute(
        "INSERT INTO outcomes (assessment_id, guidance_packet_id, hash_version, terminal_status, "
        "detail_json, recorded_at, prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1, guidance, 2, status, detail_json, recorded_at, previous_hash,
            dbmod._row_hash(previous_hash, content),
        ),
    )
    assert store.validate_chain() is True
    assert store.materialize_learning_context(assessment_id) is None
    assert store.chain_status()["counts"]["learning_context"] == 0
    store.close()


@pytest.mark.parametrize("byte_limit", [True, "4096", None, -1, 10**100])
def test_recall_validates_byte_limit_without_raising(tmp_path: Path, byte_limit) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    assessment_id = _verified_completed(store, task="bounded input")
    assert store.materialize_learning_context(assessment_id) is not None
    recall = store.recall_learning_context("r1", "bounded", byte_limit=byte_limit)
    assert recall.status in {"available", "empty", "unavailable"}
    store.close()


def test_recall_fetches_and_parses_only_a_fixed_candidate_window(
    tmp_path: Path, monkeypatch
) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    for index in range(RECALL_CANDIDATE_WINDOW + 6):
        assessment_id = _verified_completed(store, task=f"window common {index:03}")
        assert store.materialize_learning_context(assessment_id) is not None

    # Isolate FTS result parsing from the independent canonical-chain audit above it.
    monkeypatch.setattr(store, "_validate_assessment_chain", lambda: True)
    monkeypatch.setattr(store, "_validate_outcome_chain", lambda: True)
    monkeypatch.setattr(store, "_validate_guardrail_chain", lambda: True)
    monkeypatch.setattr(store, "_validate_learning_context_chain", lambda: True)
    inspected = 0
    original = store._learning_entry

    def counted(row):
        nonlocal inspected
        inspected += 1
        return original(row)

    monkeypatch.setattr(store, "_learning_entry", counted)
    recall = store.recall_learning_context("r1", "common", byte_limit=32768)
    assert inspected <= RECALL_CANDIDATE_WINDOW
    assert len(recall.entries) == 5
    assert recall.truncated is True
    store.close()


def test_read_only_legacy_store_without_learning_schema_degrades_cleanly(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    store = SqliteStore(str(db))
    store._con.execute("DROP TABLE learning_context_fts")
    store._con.execute("DROP TABLE learning_context")
    store.close()

    readonly = SqliteStore(str(db), read_only=True)
    assert readonly.chain_status()["counts"]["learning_context"] == 0
    assert readonly.recall_learning_context("r1", "anything").status == "unavailable"
    readonly.close()
