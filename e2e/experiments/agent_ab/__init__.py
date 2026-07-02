"""PEBRA agent-A/B efficacy experiment (paired, blinded, instrumented pilot trial).

The real coding-agent runner is implemented and live-gated. The supported entry point is
``nox -s e2e-ab``: it supplies the non-secret run gates and defaults the external repo path when
``../avalonia_template`` exists; the user still supplies ``ANTHROPIC_API_KEY``. Direct orchestrator
calls remain stricter and require the gate variables explicitly. See README.md for the pre-registered
endpoints, the blinding invariant, and the honest non-claims.
"""
