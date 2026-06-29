# Benchmark corpora

Each corpus is a set of recorded `(assessment-input, outcome-label)` cases the flow tier replays.

- **`fixtures/`** — small, hand-authored corpus. Proves the *machinery* (wiring, determinism, that
  promotion can move a scorecard) — **not** real-world learning quality. Calibrated so promotion
  provably improves Brier, so a green run means "the loop is wired and deterministic."
- **JIT / SZZ corpora** (later phase) — real labeled commits (SZZ via pydriller, generated offline and
  committed as manifests). This is where the *credible* "calibration improves vs genesis" claim comes
  from. CI never runs pydriller live; it replays the committed manifests.

> A green fixture run is a wiring proof, not a quality proof. The real calibration proof is the JIT/SZZ
> tier.
