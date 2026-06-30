"""Learning-loop replay benchmark — drive the fixture corpus through the REAL learning loop, two tracks.

GENESIS track: a fresh DB, no promotion. It is structurally snapshot-free (zero fact/snapshot rows),
so it cannot apply a learned override even if asked. Its scorecard is the baseline.

LEARNED track: a fresh DB seeded with the 120 ``promote`` cases -> real ``measure_learning`` ->
``run_promotion`` (the ONLY way a fact is written — never hand-inserted) -> ``SnapshotReadStore`` ->
``apply_snapshot`` on the 50 disjoint ``score`` cases. Its scorecard reflects the learned override.

Every metric is delegated to ``benchmarks.flow.scorecard`` (which delegates to ``pebra.core``). The heavy
adapter/app imports are lazy (inside the loop functions) so the pure helpers + their unit tests import
under ``--no-deps``. Determinism target is the scorecard JSON, never the SQLite bytes (the DB carries
wall-clock ``recorded_at`` in its hash chain).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from benchmarks.flow import scorecard as sc
from benchmarks.flow.corpus import export_fixture as corpus_mod
from pebra.core import learning_eval as le
from pebra.core.apply_snapshot import apply_snapshot
from pebra.core.models import AssessmentInput, AssessmentRequest

REPO_ID = "bench-flow"
RESULTS_DIR = Path(__file__).parent / "results"
RISK_BINARY = "risk_binary"


def _by_partition(corpus, partition: str) -> list[tuple[dict, dict, dict]]:
    """(case, prediction, outcome) triples for one partition, joined by case_id."""
    pred_by = {p["case_id"]: p for p in corpus.predictions}
    out_by = {o["case_id"]: o for o in corpus.outcomes}
    return [
        (c, pred_by[c["case_id"]], out_by[c["case_id"]])
        for c in corpus.cases if c["partition"] == partition
    ]


def _minimal_inp(p_success: float) -> AssessmentInput:
    """The smallest valid AssessmentInput for apply_snapshot to override a global p_success fact (the
    fields apply_snapshot reads: p_success, events, structural_features). If AssessmentInput gains a new
    REQUIRED field this raises TypeError — intentional, forces the maintainer to update it (not silent)."""
    req = AssessmentRequest.single_action(
        task="t", action_id="a1", label="x", action_type="edit", expected_files=["src/a.py"],
    )
    return AssessmentInput(
        request=req, action=req.candidate_actions[0], events=[], p_success=p_success,
        immediate_benefit=0.5, review_cost=0.1, criticality_stage="C2", criticality_value=0.5,
        edit_confidence_factors={}, thresholds={}, repo_id=REPO_ID, repo_root="/x",
        structural_features=None,
    )


def _outcome(actual_success) -> le.DecisionOutcome:
    return le.DecisionOutcome(
        proceeded=True, harmful=not bool(actual_success), criticality_stage="C2",
    )


def _seed_case(store, learning_port, case: dict, pred: dict, outcome: dict) -> str:
    """One real loop step (mirrors tests/integration/test_ignition_loop): persist the authored
    prediction -> record the terminal outcome -> measure. ``measure_learning`` stamps shadow_mode=0 for
    'completed', so the row enters the production calibration view."""
    from pebra.app import learning_controller as lc

    result = _make_result()
    asm = store.persist_assessment(
        result, {"task": "t", "action_id": case["action_id"]},
        predictions=[{
            "action_id": case["action_id"], "target_type": pred["target_type"],
            "target_name": pred["target_name"], "predicted_value": pred["predicted_value"],
            "features": pred["features"],
        }],
    )
    store.record_outcome(asm, outcome["terminal_status"], {"actual_success": bool(outcome["actual_success"])})
    lc.measure_learning(asm, store=store, learning_port=learning_port)
    return asm


def _make_result():
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    # model_guidance_packet=None so the prediction_error row carries guidance_packet_id IS NULL and the
    # row is eligible for the production calibration view (a guided row would be filtered out).
    return AssessmentResult(
        recommended_decision=Decision.PROCEED, requires_confirmation=False,
        action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
        scores={"benefit": 0.5}, repo_id=REPO_ID, repo_root="/x", model_guidance_packet=None,
    )


def run_genesis_replay(corpus, db_path: Path) -> dict:
    """Baseline: seed the score cases, measure, score them straight (no snapshot). Asserts genesis is
    structurally snapshot-free via ``snapshot_loaded``."""
    from pebra.adapters.learning_store import LearningStore
    from pebra.adapters.snapshot_read_store import SnapshotReadStore
    from pebra.adapters.store.db import SqliteStore

    store = SqliteStore(str(db_path))
    try:
        port = LearningStore(store)
        for case, pred, outcome in _by_partition(corpus, "score"):
            _seed_case(store, port, case, pred, outcome)
        rows = store.load_production_calibration_rows(REPO_ID, RISK_BINARY)
        pairs = [(float(r["predicted_probability"]), int(r["actual_outcome"])) for r in rows]
        outcomes = [_outcome(r["actual_outcome"]) for r in rows]
        bundle = SnapshotReadStore(store).load_active_snapshot(REPO_ID)  # MUST be None for genesis
        chain_valid = store.validate_chain()
    finally:
        store.close()
    return {
        "scorecard": sc.build_scorecard(pairs, outcomes, label="genesis"),
        "chain_valid": chain_valid,
        "snapshot_loaded": bundle is not None,
        "n": len(pairs),
    }


def run_learned_replay(corpus, db_path: Path) -> dict:
    """Seed promote cases, run REAL promotion, read the snapshot, apply it to the disjoint score cases."""
    from pebra.adapters.learning_store import LearningStore
    from pebra.adapters.snapshot_read_store import SnapshotReadStore
    from pebra.adapters.store.db import SqliteStore
    from pebra.app.promotion_controller import run_promotion

    store = SqliteStore(str(db_path))
    try:
        port = LearningStore(store)
        for case, pred, outcome in _by_partition(corpus, "promote"):
            _seed_case(store, port, case, pred, outcome)
        n_promote_rows = len(store.load_production_calibration_rows(REPO_ID, RISK_BINARY))
        expected = len(_by_partition(corpus, "promote"))
        if n_promote_rows != expected:
            # a store filter silently dropped rows -> promotion would fire on a different distribution
            # than the authored fixture (the 100..expected-1 shortfall the gate can't see). Fail loud.
            raise RuntimeError(
                f"expected {expected} production calibration rows before promotion, got "
                f"{n_promote_rows}; a store filter is dropping seeded rows"
            )
        promo = run_promotion(REPO_ID, store=store, learning_port=port)
        bundle = SnapshotReadStore(store).load_active_snapshot(REPO_ID)
        chain_valid = store.validate_chain()

        pairs: list[tuple[float, int]] = []
        outcomes: list[le.DecisionOutcome] = []
        for _case, pred, outcome in _by_partition(corpus, "score"):
            learned_p = apply_snapshot(_minimal_inp(float(pred["predicted_value"])), bundle).p_success
            pairs.append((learned_p, int(bool(outcome["actual_success"]))))
            outcomes.append(_outcome(outcome["actual_success"]))
    finally:
        store.close()
    return {
        "scorecard": sc.build_scorecard(pairs, outcomes, label="learned"),
        "chain_valid": chain_valid,
        "promotion_fired": promo.promoted,
        "snapshot_loaded": bundle is not None,
        "n_promote_rows": n_promote_rows,
        "veto_reasons": list(promo.veto_reasons),
        "n": len(pairs),
    }


def replay_all(corpus, base_dir: Path) -> dict:
    """Run both tracks in isolated DBs under ``base_dir`` and return both result dicts."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    return {
        "genesis": run_genesis_replay(corpus, base / "genesis.db"),
        "learned": run_learned_replay(corpus, base / "learned.db"),
    }


def main(argv: list[str] | None = None) -> int:
    corpus = corpus_mod.load_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        results = replay_all(corpus, Path(tmp))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "genesis_scorecard.json").write_text(
        sc.to_json(results["genesis"]["scorecard"]) + "\n", encoding="utf-8"
    )
    (RESULTS_DIR / "learned_scorecard.json").write_text(
        sc.to_json(results["learned"]["scorecard"]) + "\n", encoding="utf-8"
    )
    g = results["genesis"]["scorecard"]["metrics"]["brier"]
    learned = results["learned"]
    print(json.dumps({
        "promotion_fired": learned["promotion_fired"],
        "chain_valid_genesis": results["genesis"]["chain_valid"],
        "chain_valid_learned": learned["chain_valid"],
        "genesis_brier": g,
        "learned_brier": learned["scorecard"]["metrics"]["brier"],
    }, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
