"""Phase 3a — `pebra record-outcome` CLI end-to-end against a temp store."""

from __future__ import annotations

from pebra.adapters.store.db import SqliteStore
from pebra.cli.main import main
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _seed_assessment(db_path: str, repo_root: str) -> str:
    store = SqliteStore(db_path)
    asm = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={},
            repo_id="r",
            repo_root=repo_root,
        ),
        {
            "task": "t",
            "action_id": "a1",
            "revision_envelope": {
                "expected_files": ["src/auth.py"],
                "public_symbols": ["auth.login"],
            },
        },
    )
    store.close()
    return asm


def test_record_outcome_cli_writes_outcome(tmp_path) -> None:
    (tmp_path / ".pebra").mkdir()
    db = str(tmp_path / ".pebra" / "pebra.db")
    asm = _seed_assessment(db, str(tmp_path))

    rc = main(
        ["record-outcome", "--assessment-id", asm, "--status", "skipped",
         "--repo-root", str(tmp_path), "--db", db]
    )
    assert rc == 0

    store = SqliteStore(db)
    outs = store.load_outcomes(asm)
    store.close()
    assert outs and outs[0]["terminal_status"] == "skipped"


def test_record_outcome_cli_bad_detail_json_errors_cleanly(tmp_path) -> None:
    (tmp_path / ".pebra").mkdir()
    db = str(tmp_path / ".pebra" / "pebra.db")
    asm = _seed_assessment(db, str(tmp_path))

    rc = main(
        ["record-outcome", "--assessment-id", asm, "--status", "completed",
         "--detail", "{not valid json", "--repo-root", str(tmp_path), "--db", db]
    )
    assert rc == 2  # clean error, not a traceback

    store = SqliteStore(db)
    assert store.load_outcomes(asm) == []  # nothing recorded on bad input
    store.close()


def test_record_outcome_cli_unknown_assessment_errors_cleanly(tmp_path) -> None:
    (tmp_path / ".pebra").mkdir()
    db = str(tmp_path / ".pebra" / "pebra.db")
    SqliteStore(db).close()  # create the schema, no assessments

    rc = main(
        ["record-outcome", "--assessment-id", "asm_999", "--status", "completed",
         "--repo-root", str(tmp_path), "--db", db]
    )
    assert rc == 2  # KeyError -> clean exit, not a traceback


def test_verified_completed_cli_materializes_learning_context(tmp_path) -> None:
    (tmp_path / ".pebra").mkdir()
    db = str(tmp_path / ".pebra" / "pebra.db")
    asm = _seed_assessment(db, str(tmp_path))
    store = SqliteStore(db)
    store.persist_guardrails(
        asm, {"pre_commit_decision": "proceed", "measured_benefit": 0.25}
    )
    store.close()

    assert main([
        "record-outcome", "--assessment-id", asm, "--status", "completed",
        "--detail", '{"lesson":"do not trust"}',
        "--repo-root", str(tmp_path), "--db", db,
    ]) == 0

    store = SqliteStore(db)
    entry = store.learning_context_for_assessment(asm)
    store.close()
    assert entry is not None
    assert entry.measured_benefit == 0.25
    assert "do not trust" not in entry.lesson
