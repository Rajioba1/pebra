# PEBRA benchmarks (Phase 5b)

A standalone validation harness that **drives the real engine**. It imports `pebra` freely; production
code must never import `benchmarks` (enforced by the `pebra-no-benchmarks` import-linter contract). It
is not shipped — `pyproject` packages only `pebra*`.

## The two claims this harness separates

1. **Wiring + determinism** (fixture-fast). A small hand-authored corpus proves the loop is wired
   correctly and reproducible. **A green fixture run does NOT mean "PEBRA learns well"** — the fixture
   is calibrated so promotion provably helps; it proves *machinery*, not real-world quality.
2. **Real calibration proof** (JIT/SZZ, later phase). Real labeled commits where the learned track must
   actually beat the genesis track. This is the credible claim, and it is deliberately a later tier so
   the harness can exist and be trusted before the heavy corpus work begins.

> Do not read a green `bench` badge as "calibration is good." It means "the loop is wired and
> deterministic." The learning-quality claim lives in the JIT/SZZ tier.

## Layout

```
benchmarks/
  README.md
  math/                 # CSV -> reference artifact + PEBRA artifact -> comparison artifact
    data/               # committed prediction/outcome fixture CSV
    results/            # committed reference_metrics, pebra_metrics, comparison JSON
  flow/
    scorecard.py        # normalized, deterministic scorecard JSON (delegates metrics to pebra.core)
    corpus/             # fixture corpus now; JIT/SZZ corpora later
    e2e/                # real backend-loop wiring proof (later phase)
```

`tests/oracles/` stays the **fast CI subset** (pass/fail, machine-precision). `benchmarks/math/` is the
**heavier tier** that mirrors the Tauri validation style:

```
data/prediction_errors.csv -> results/reference_metrics.json   # sklearn/numpy lane
data/prediction_errors.csv -> results/pebra_metrics.json       # pebra.core lane
reference_metrics + pebra_metrics -> results/comparison.json   # no coercion
```

Any metric outside tolerance fails `comparison.json`; there is no known-divergence bypass.

## Running Validation

There is currently **no** `pebra --benchmark` or `pebra benchmark` CLI command. The benchmark harness is
kept outside the production CLI so benchmark-only oracle dependencies and fixtures cannot leak into
the decision surface. Use the module/nox commands below.

Math validation, Tauri-style:

```powershell
# Full math benchmark test suite.
nox -s bench-math

# Regenerate the CSV fixture and all math artifacts.
nox -s bench-math-regen

# Equivalent manual artifact flow:
python -m benchmarks.math.export_fixture
python -m benchmarks.math.reference_metrics
python -m benchmarks.math.pebra_metrics
python -m benchmarks.math.compare

# Convenience runner: computes reference + PEBRA + comparison from the CSV.
python -m benchmarks.math.run

# Convenience runner, also writes results/*.json artifacts.
python -m benchmarks.math.run --write
```

Generated math artifacts:

```text
benchmarks/math/data/prediction_errors.csv
benchmarks/math/results/reference_metrics.json
benchmarks/math/results/pebra_metrics.json
benchmarks/math/results/comparison.json
```

Flow validation:

```powershell
nox -s bench-flow
python -m pytest benchmarks/flow -q
```

Production CLI loop, for manual end-to-end data creation:

```powershell
pebra setup-graph --fix
pebra assess --help
pebra verify --help
pebra record-outcome --help
pebra learn --assessment-id <assessment_id>
pebra promote --repo-id <repo_id>
pebra scorecard --repo-id <repo_id>
```

## Determinism target

The stable artifact is the **normalized `scorecard.json`**, NOT the SQLite DB. The DB's append-only
hash chain carries wall-clock `recorded_at`, so it is never byte-identical across runs — and that is the
wrong invariant. The scorecard is computed purely from `(prediction, outcome)` pairs:

```
same corpus + same snapshot + same PEBRA commit  ->  same normalized scorecard.json
```

## What is reused vs benchmark-only

- **Reused from `pebra.core` (never re-derived):** Brier / log-loss / MSE / bias
  (`prediction_error`), ECE / false-proceed / false-block / lift / `compute_promotion_metrics`
  (`learning_eval`), promotion gates (`promotion_evaluator`), learned-fact reapplication
  (`apply_snapshot`), the canonical snapshot decoder (`SnapshotReadStore`), and the live loop
  (`learning_controller` / `promotion_controller`).
- **Benchmark-only references:** numpy/sklearn/scipy for oracle validation; pandas/matplotlib for
  reports/plots (later). These are dev/`bench` extras and are never imported by `pebra`.

## Build status

- **Phase 1-3 (this slice):** scaffold + import wall · math oracle layer · deterministic scorecard JSON.
- **Later (deferred):** replay over the fixture corpus · `flow/e2e/` backend-wiring proof ·
  `bench-math` / `bench-flow` nox sessions · JIT/SZZ corpus (real proof) · Arm 2b agent A/B (needs the
  risk-memory layer first).

Guidance isolation (Arm 2a) is proven at runtime in `flow/e2e/test_guidance_isolation.py` (later),
rather than via a static intra-benchmarks import contract.
