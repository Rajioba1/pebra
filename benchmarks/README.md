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
  math/                 # formula validation vs numpy/sklearn/scipy + JSON report output
  flow/
    scorecard.py        # normalized, deterministic scorecard JSON (delegates metrics to pebra.core)
    corpus/             # fixture corpus now; JIT/SZZ corpora later
    e2e/                # real backend-loop wiring proof (later phase)
```

`tests/oracles/` stays the **fast CI subset** (pass/fail, machine-precision). `benchmarks/math/` is the
**heavier tier** that produces an inspectable, regenerable validation report.

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
