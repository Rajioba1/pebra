"""PEBRA true agent/product end-to-end suite (Tauri-standard).

Real workflows over the REAL boundaries: a scripted agent reaches PEBRA only through the CLI/MCP
subprocess (never `import pebra` internals — enforced by ``test_boundary_discipline``), drives the
assess → record-outcome → learn → promote → reassess cycle, and a human reviews dashboard screenshots.

Current coverage includes agent-CLI seeded learning, dashboard metrics/visual review, and the gated
external CodeGraph/dotnet lane. The remaining full Tauri-level additions are the organic learning lane
and agent A/B efficacy.
This package is never shipped (pyproject packages only ``pebra*``).
"""
