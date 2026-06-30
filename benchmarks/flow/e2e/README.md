# E2E Benchmark Notes

This folder is reserved for backend-loop wiring tests: `assess -> capture -> record-outcome ->
measure -> promote -> apply`. It is not the math-oracle layer itself.

## Current Runnable Validation

Math validation currently follows the Tauri-style artifact split:

```text
prediction_errors.csv -> reference_metrics.json   # sklearn/numpy oracle lane
prediction_errors.csv -> pebra_metrics.json       # PEBRA implementation lane
reference_metrics + pebra_metrics -> comparison.json
```

Run it with:

```powershell
nox -s bench-math
python -m benchmarks.math.run
```

Regenerate committed artifacts with:

```powershell
nox -s bench-math-regen
```

Manual equivalent:

```powershell
python -m benchmarks.math.export_fixture
python -m benchmarks.math.reference_metrics
python -m benchmarks.math.pebra_metrics
python -m benchmarks.math.compare
```

The comparison is strict: every metric must be within tolerance. There is no known-divergence bypass
and no coerced pass.

## PEBRA CLI Syntax

There is no `pebra --benchmark` or `pebra benchmark` command today. Benchmarks are intentionally run
through `nox` or `python -m benchmarks...` so benchmark-only oracle dependencies stay outside the
production CLI.

The production loop commands used to create real learning data are:

```powershell
pebra setup-graph --fix
pebra assess --help
pebra verify --help
pebra record-outcome --help
pebra learn --assessment-id <assessment_id>
pebra promote --repo-id <repo_id>
pebra scorecard --repo-id <repo_id>
```

Use `--help` on each command for its required input shape. The benchmark harness should call the app
and store layers directly when it needs deterministic fixtures; the CLI commands are for operator
workflows and manual smoke checks.

## Not Yet Proven Here

A green math benchmark proves formula/oracle agreement on the committed fixture. It does not prove
that PEBRA learns well on real repositories. That requires the later JIT/SZZ corpus replay and agent
efficacy A/B arm.

## Public vs Private Artifacts

The e2e framework, docs, small synthetic fixtures, and frozen public JSON artifacts should stay
tracked. Private corpora, raw JIT/SZZ exports, local agent-run traces, and bulky generated outputs
should not be committed.

Ignored paths:

```text
benchmarks/flow/e2e/out/
benchmarks/flow/e2e/runs/
benchmarks/flow/e2e/tmp/
benchmarks/flow/corpus/private/
benchmarks/flow/corpus/jit_szz/raw/
```
