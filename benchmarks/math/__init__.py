"""Math validation tier — heavier formula validation with JSON/report output.

Distinct from ``tests/oracles/`` (the fast, pass/fail CI subset): this tier produces an inspectable,
regenerable validation report (PEBRA value vs reference value vs abs-diff vs pass) and may use the
heavier reference stack. It NEVER re-derives PEBRA's math — it validates ``pebra.core`` against
numpy/sklearn/scipy references.
"""
