"""Author the Tier B fixture corpus (offline) and write it as committed JSONL.

Three parallel files keyed by ``case_id``: ``cases.jsonl`` (partition + identity), ``predictions.jsonl``
(the authored genesis prediction), ``outcomes.jsonl`` (the terminal label). A 120-case ``promote``
partition drives real promotion (>= MIN_CALIBRATION_SAMPLES); a disjoint 50-case ``score`` partition is
the out-of-corpus holdout the learned fact is applied to.

HONEST SCOPE: this is a SYNTHETIC fixture — a wiring proof, not a calibration-quality claim. The score
partition is authored at the same success rate the model learns, so the learned track beats genesis BY
CONSTRUCTION. What the gate actually proves is that the loop is WIRED (promotion fires, the fact is
written, the snapshot reads back, apply_snapshot moves the prediction, chains validate, replay is
deterministic). The real out-of-sample quality signal belongs to the future JIT/SZZ tier.

Regenerate with ``python -m benchmarks.flow.corpus.export_fixture`` (or ``nox -s bench-flow-regen``).
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from pathlib import Path

from pebra.core.constants import MIN_CALIBRATION_SAMPLES
from pebra.core import promotion_evaluator as pe

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPO_ID = "bench-flow"
CRITICALITY_STAGE = "C2"  # low criticality (safe for false_block_rate_c0_c2)

_CASES_JSONL = "cases.jsonl"
_PREDICTIONS_JSONL = "predictions.jsonl"
_OUTCOMES_JSONL = "outcomes.jsonl"


@dataclass
class Corpus:
    cases: list[dict]
    predictions: list[dict]
    outcomes: list[dict]
    genesis_p: float
    success_rate: float


def _success_flags(n: int, rate: float, rng: random.Random) -> list[bool]:
    """Exactly ``round(n*rate)`` successes, placed at seed-shuffled positions — exact rate AND
    deterministic (a per-case coin flip would drift the rate run to run)."""
    k = round(n * rate)
    idx = list(range(n))
    rng.shuffle(idx)
    chosen = set(idx[:k])
    return [i in chosen for i in range(n)]


def build_corpus(
    n_promote: int = 120,
    n_score: int = 50,
    *,
    success_rate: float = 0.55,
    genesis_p: float = 0.35,
    seed: int = 42,
) -> Corpus:
    rng = random.Random(seed)
    promote_flags = _success_flags(n_promote, success_rate, rng)
    score_flags = _success_flags(n_score, success_rate, rng)

    cases: list[dict] = []
    predictions: list[dict] = []
    outcomes: list[dict] = []

    def _add(partition: str, n: int, flags: list[bool], start: int) -> None:
        for i in range(n):
            cid = f"c{start + i:04d}"
            cases.append({
                "case_id": cid, "partition": partition, "repo_id": REPO_ID,
                "action_id": f"a-{start + i:04d}", "criticality_stage": CRITICALITY_STAGE,
            })
            predictions.append({
                "case_id": cid, "target_type": "risk_binary", "target_name": "p_success",
                "predicted_value": genesis_p, "features": {},
            })
            outcomes.append({
                "case_id": cid, "terminal_status": "completed", "actual_success": flags[i],
            })

    _add("promote", n_promote, promote_flags, 1)
    _add("score", n_score, score_flags, n_promote + 1)
    return Corpus(cases, predictions, outcomes, genesis_p, success_rate)


def validate_coverage(corpus: Corpus) -> None:
    """Refuse to write a fixture that can't fire promotion or can't improve. Imports
    MIN_CALIBRATION_SAMPLES so a future increase to the floor fails here, not silently at promote time."""
    by_id = {o["case_id"]: o["actual_success"] for o in corpus.outcomes}
    promote = [c for c in corpus.cases if c["partition"] == "promote"]
    score = [c for c in corpus.cases if c["partition"] == "score"]
    if len(promote) < MIN_CALIBRATION_SAMPLES + 20:
        raise RuntimeError(
            f"promote partition {len(promote)} < MIN_CALIBRATION_SAMPLES+20 "
            f"({MIN_CALIBRATION_SAMPLES + 20}); promotion would not fire"
        )
    if len(score) < 10:
        raise RuntimeError(f"score partition {len(score)} < 10; scorecard would be too noisy")
    rate = sum(by_id[c["case_id"]] for c in promote) / len(promote)
    if rate - corpus.genesis_p < 0.05:
        raise RuntimeError(
            f"promote empirical rate {rate:.3f} does not clear genesis_p {corpus.genesis_p} by >=0.05; "
            "the learned fact could not improve the score track"
        )
    predicted_by_id = {row["case_id"]: row for row in corpus.predictions}
    gate = pe.evaluate_promotion_gate(
        pe.CandidateFact(
            target_name="p_success",
            target_type="risk_binary",
            scope_kind="global",
            scope_value="",
            value=rate,
            sample_size=len(promote),
        ),
        [
            {
                "actual_outcome": int(bool(by_id[case["case_id"]])),
                "predicted_probability": float(
                    predicted_by_id[case["case_id"]]["predicted_value"]
                ),
                "features": predicted_by_id[case["case_id"]].get("features") or {},
            }
            for case in promote
        ],
        pe.PromotionConfig(),
    )
    if not gate.promoted:
        raise RuntimeError(
            "promote partition does not pass the production promotion gate: "
            f"{gate.veto_reason}"
        )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_corpus(corpus: Corpus, corpus_dir: Path = FIXTURES_DIR) -> None:
    validate_coverage(corpus)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(corpus_dir / _CASES_JSONL, corpus.cases)
    _write_jsonl(corpus_dir / _PREDICTIONS_JSONL, corpus.predictions)
    _write_jsonl(corpus_dir / _OUTCOMES_JSONL, corpus.outcomes)


def load_corpus(corpus_dir: Path = FIXTURES_DIR) -> Corpus:
    cases = _read_jsonl(corpus_dir / _CASES_JSONL)
    predictions = _read_jsonl(corpus_dir / _PREDICTIONS_JSONL)
    outcomes = _read_jsonl(corpus_dir / _OUTCOMES_JSONL)
    genesis_p = predictions[0]["predicted_value"] if predictions else 0.0
    by_id = {o["case_id"]: o["actual_success"] for o in outcomes}
    promote = [c for c in cases if c["partition"] == "promote"]
    success_rate = (
        sum(by_id[c["case_id"]] for c in promote) / len(promote) if promote else 0.0
    )
    return Corpus(cases, predictions, outcomes, genesis_p, success_rate)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Author the Tier B fixture corpus.").parse_args(argv)
    corpus = build_corpus()
    write_corpus(corpus)
    print(f"wrote corpus to {FIXTURES_DIR} ({len(corpus.cases)} cases)")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
