from __future__ import annotations

import json

from pebra.adapters.store.db import SqliteStore
from pebra.cli.main import main
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def test_finalize_outcome_cli_is_idempotent_end_to_end(tmp_path) -> None:
    (tmp_path / ".pebra").mkdir()
    db = str(tmp_path / ".pebra" / "pebra.db")
    store = SqliteStore(db)
    assessment_id = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.ASK_HUMAN,
            requires_confirmation=True,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={},
            repo_id="r",
            repo_root=str(tmp_path),
        ),
        {"task": "t"},
        predictions=[{
            "target_type": "risk_binary", "target_name": "p_success",
            "predicted_value": 0.5, "features": {},
        }],
    )
    store.close()
    sidecar = tmp_path / "outcome.json"
    sidecar.write_text(json.dumps({
        "assessment_id": assessment_id,
        "status": "skipped",
        "detail": {"actual_success": False},
    }), encoding="utf-8")
    argv = [
        "finalize-outcome", "--trusted-outcome-file", str(sidecar),
        "--repo-root", str(tmp_path), "--db", db, "--json",
    ]
    assert main(argv) == 0
    assert main(argv) == 0

    store = SqliteStore(db)
    assert len(store.load_outcomes(assessment_id)) == 1
    assert len(store.load_prediction_errors("r")) == 1
    assert store.validate_chain() is True
    store.close()
