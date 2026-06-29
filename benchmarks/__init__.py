"""PEBRA benchmarks (Phase 5b) — a standalone validation harness that DRIVES the real engine.

This is a top-level package (NOT under ``pebra/``) on purpose: it imports ``pebra`` freely, but
production code must never import ``benchmarks`` (enforced by the ``pebra-no-benchmarks`` import-linter
contract). It is not shipped — ``pyproject`` packages only ``pebra*``.

Layout (see ``benchmarks/README.md``):
  - ``benchmarks/math/``      formula validation vs numpy/sklearn/scipy + JSON report output
  - ``benchmarks/flow/``      replay + deterministic scorecard
  - ``benchmarks/flow/e2e/``  real backend-loop wiring proof (later phase)
  - ``benchmarks/flow/corpus/`` fixture corpus now; JIT/SZZ corpora later
"""
