# Tier B fixture corpus

**Synthetic fixture — a wiring proof, NOT a calibration-quality claim.**

170 authored cases the replay drives through the real learning loop:
- **`promote` (120 cases)** — drive the real `measure → promote` path. 120 ≥ `MIN_CALIBRATION_SAMPLES`
  (100), so promotion actually fires.
- **`score` (50 cases)** — a disjoint holdout never seen by promotion; the learned fact is applied to
  these and scored.

Authored shape (deterministic, `random.Random(42)`):
- genesis prediction = **0.70** for every case (a flat, miscalibrated prior).
- actual success rate ≈ **0.85** (promote 102/120, score 42/50).
- learned fact = empirical mean of the promote set ≈ **0.85**.

## Why "learned beats genesis" here is wiring, not generalization
The score partition is authored at the **same** success rate the model learns, so applying `0.85`
beats the flat `0.70` **by construction**. That is intentional and is stated plainly: this corpus
proves the **loop is wired**, not that PEBRA is well-calibrated. The real, can-actually-fail
out-of-sample quality signal belongs to the future **JIT/SZZ tier**.

## What the gate actually catches (it must fail if any of these break)
- promotion does not fire (`< MIN_CALIBRATION_SAMPLES`, or the gate vetoes)
- the learned fact is not written
- the snapshot read misses the fact
- `apply_snapshot` does not move the prediction
- a hash chain fails to validate
- replay is non-deterministic
- the learned scorecard does not strictly improve on the authored fixture

No coercion: `comparison.json` records `passed=false` + a `failure_reasons` entry for any of the above.

## Files
| file | one row per | keys |
|---|---|---|
| `cases.jsonl` | case | `case_id, partition, repo_id, action_id, criticality_stage` |
| `predictions.jsonl` | case | `case_id, target_type, target_name, predicted_value, features` |
| `outcomes.jsonl` | case | `case_id, terminal_status, actual_success` |

Regenerate: `python -m benchmarks.flow.corpus.export_fixture` (or `nox -s bench-flow-regen`).
