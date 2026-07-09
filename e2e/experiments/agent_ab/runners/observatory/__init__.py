"""Dev-only run observatory: a thin, read-only shell over the agent A/B assay's run artifacts.

Reads e2e/out/ab/<run-id>/ (outcomes.json + run_status.json + preflight/coverage.json + per-arm
pebra.db stores), renders a live run index / scoreboard / matrix, and drills down into the REAL
`pebra dashboard` per arm by shelling it out. NEVER imports pebra; NEVER writes into a run dir.
"""
