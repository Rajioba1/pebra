# Learning-loop replay benchmark (wiring proof)

This is a **deterministic wiring proof**, NOT an agent/product e2e. It drives the synthetic
`benchmarks/flow/corpus` fixture through the real learning machinery (`measure → promote →
SnapshotReadStore → apply_snapshot`) and asserts the loop is wired and reproducible.

It starts from **authored prediction rows**, not from `assess_controller.assess()` evidence gathering —
there is no agent, no real git repo, no codegraph, and no dashboard here.

```text
fixture corpus -> genesis replay (no snapshot)  ┐
fixture corpus -> learned replay (promote+apply) ┘-> compare -> comparison.json (passed / failure_reasons)
```

Run it:

```powershell
nox -s bench-flow            # runs the wiring + scorecard unit tests
nox -s bench-flow-regen      # regenerates the committed fixtures/results (byte-stable)
```

## What it proves vs what it does NOT
- **Proves:** the loop machinery — promotion fires, the fact is written, the snapshot reads back,
  `apply_snapshot` moves the prediction, chains validate, replay is deterministic. No coercion: any
  wiring break sets `comparison.json` `passed=false` with a `failure_reasons` entry.
- **Does NOT prove:** that an agent can use PEBRA on real code, or that PEBRA calibrates well on real
  repositories. Those are the **agent/product e2e** (repo-root `e2e/`) and the later **JIT/SZZ** quality
  tier, respectively.

> The "learned beats genesis" result here is true *by construction* of the synthetic fixture — a wiring
> proof, not a calibration-quality claim. See `benchmarks/README.md`.
