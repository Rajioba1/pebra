"""PEBRA true agent/product end-to-end suite (Tauri-standard).

Real workflows over the REAL boundaries: a scripted agent reaches PEBRA only through the CLI/MCP
subprocess (never `import pebra` internals — enforced by ``test_boundary_discipline``), drives the
assess → record-outcome → learn → promote → reassess cycle, and a human reviews dashboard screenshots.

SCOPE NOTE: the current first slice (`features/agent` + `features/learning` + `features/dashboard`) is
the agent-CLI seeded-learning + dashboard-visual e2e. Full Tauri-level PEBRA coverage additionally
requires the codegraph graph feature (gated E2E_CODEGRAPH) and the organic learning lane (nightly).
This package is never shipped (pyproject packages only ``pebra*``).
"""
