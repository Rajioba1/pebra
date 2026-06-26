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
        {"task": "t"},
    )
    store.close()
    return asm


def test_record_outcome_cli_writes_outcome(tmp_path) -> None:
    (tmp_path / ".pebra").mkdir()
    db = str(tmp_path / ".pebra" / "pebra.db")
    asm = _seed_assessment(db, str(tmp_path))

    rc = main(
        ["record-outcome", "--assessment-id", asm, "--status", "completed",
         "--repo-root", str(tmp_path), "--db", db]
    )
    assert rc == 0

    store = SqliteStore(db)
    outs = store.load_outcomes(asm)
    store.close()
    assert outs and outs[0]["terminal_status"] == "completed"


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
