"""Learning-loop replay benchmark wiring proof.

This package is deliberately not the product e2e. It drives authored predictions through the real
record_outcome -> learn -> promote -> snapshot-read -> apply machinery and asserts deterministic
scorecard artifacts.
"""
