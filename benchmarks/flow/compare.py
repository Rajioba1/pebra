"""Tier B comparison — reconcile the genesis and learned track results into a verdict.

NO COERCION. ``passed`` is True only when EVERY wiring invariant holds. Any break is recorded as an
explicit ``failure_reasons`` entry and ``passed=False`` — never hidden, never a tolerance fudge. The
fixture is a wiring proof (``corpus_note``), so a green verdict means "the loop is wired and
deterministic," not "PEBRA is well-calibrated."

The pass set, all required:
  - promotion fired (a fact was actually written through the real controller)
  - both hash chains validate after the replay
  - the genesis track did NOT load a snapshot (structurally snapshot-free)
  - the learned track DID load the snapshot after promotion
  - learned brier is STRICTLY less than genesis brier (no tolerance band)
  - brier_improved (the scorecard's own lift sign agrees)
  - the replay is deterministic (same scorecard JSON on re-run)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmarks.flow import replay, scorecard as sc

SCHEMA_VERSION = 1
COMPARISON_JSON = replay.RESULTS_DIR / "comparison.json"
CORPUS_NOTE = "synthetic fixture corpus — wiring proof only; not a calibration-quality claim"


def compare_replay(genesis: dict, learned: dict, *, deterministic: bool = True) -> dict:
    gcard, lcard = genesis["scorecard"], learned["scorecard"]
    cmp = sc.compare_scorecards(gcard, lcard)
    g_brier = gcard["metrics"]["brier"]
    l_brier = lcard["metrics"]["brier"]

    failure_reasons: list[str] = []
    if not learned.get("promotion_fired", False):
        failure_reasons.append(f"PROMOTION_NOT_FIRED: veto={learned.get('veto_reasons')}")
    if not learned.get("snapshot_loaded", False):
        failure_reasons.append("SNAPSHOT_NOT_LOADED_AFTER_PROMOTION")
    if genesis.get("snapshot_loaded", False):
        failure_reasons.append("GENESIS_LOADED_SNAPSHOT: genesis track must be snapshot-free")
    if not genesis.get("chain_valid", False):
        failure_reasons.append("CHAIN_INVALID: genesis")
    if not learned.get("chain_valid", False):
        failure_reasons.append("CHAIN_INVALID: learned")
    if not (l_brier < g_brier):  # strict — no tolerance band
        failure_reasons.append(f"LEARNED_NOT_BETTER: learned_brier={l_brier} >= genesis_brier={g_brier}")
    if not cmp["brier_improved"]:
        failure_reasons.append("BRIER_NOT_IMPROVED: scorecard lift sign disagrees")
    if not deterministic:
        failure_reasons.append("SCORECARD_NONDETERMINISTIC: replay produced a different scorecard JSON")

    calibration_quality = {
        "claim_scope": "synthetic_fixture_only",
        "verdict": (
            "improved_on_fixture"
            if cmp["lift"]["brier"] > 0 and cmp["lift"]["log_loss"] > 0 and cmp["lift"]["ece"] > 0
            else "mixed_on_fixture"
        ),
        "note": "synthetic fixture calibration deltas; wiring diagnostic only, not a quality claim",
        "genesis": {
            "brier": gcard["metrics"]["brier"],
            "log_loss": gcard["metrics"]["log_loss"],
            "ece": gcard["metrics"]["ece"],
        },
        "learned": {
            "brier": lcard["metrics"]["brier"],
            "log_loss": lcard["metrics"]["log_loss"],
            "ece": lcard["metrics"]["ece"],
        },
        "lift": {
            "brier": cmp["lift"]["brier"],
            "log_loss": cmp["lift"]["log_loss"],
            "ece": cmp["lift"]["ece"],
        },
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_note": CORPUS_NOTE,
        "cycle_note": (
            "authored assessment predictions -> record_outcome -> measure_learning -> run_promotion "
            "-> SnapshotReadStore -> apply_snapshot on disjoint score cases; does not run "
            "assess_controller evidence gathering"
        ),
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "promotion_fired": learned.get("promotion_fired", False),
        "chain_valid_genesis": genesis.get("chain_valid", False),
        "chain_valid_learned": learned.get("chain_valid", False),
        "genesis_loaded_snapshot": genesis.get("snapshot_loaded", False),
        "learned_loaded_snapshot": learned.get("snapshot_loaded", False),
        "deterministic": deterministic,
        "n_promote": learned.get("n_promote_rows"),
        "n_score": genesis.get("n"),
        "genesis_label": cmp["genesis_label"],
        "learned_label": cmp["learned_label"],
        "genesis_brier": g_brier,
        "learned_brier": l_brier,
        "calibration_quality": calibration_quality,
        "lift": cmp["lift"],
        "brier_improved": cmp["brier_improved"],
    }


def to_json(artifact: dict) -> str:
    return json.dumps(artifact, sort_keys=True, indent=2) + "\n"


def run_and_compare(base_dir: Path, *, check_determinism: bool = True) -> dict:
    """Drive the replay (twice if checking determinism) and produce the comparison."""
    from benchmarks.flow.corpus import export_fixture as corpus_mod

    corpus = corpus_mod.load_corpus()
    first = replay.replay_all(corpus, Path(base_dir) / "run1")
    deterministic = True
    if check_determinism:
        second = replay.replay_all(corpus, Path(base_dir) / "run2")
        deterministic = (
            sc.to_json(first["genesis"]["scorecard"]) == sc.to_json(second["genesis"]["scorecard"])
            and sc.to_json(first["learned"]["scorecard"]) == sc.to_json(second["learned"]["scorecard"])
        )
    return compare_replay(first["genesis"], first["learned"], deterministic=deterministic)


def main(argv: list[str] | None = None) -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        artifact = run_and_compare(Path(tmp))
    COMPARISON_JSON.parent.mkdir(parents=True, exist_ok=True)
    COMPARISON_JSON.write_text(to_json(artifact), encoding="utf-8")
    print(to_json(artifact), end="")
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main(sys.argv[1:]))
