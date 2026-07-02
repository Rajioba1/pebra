"""PEBRA agent-A/B efficacy experiment (paired, blinded, instrumented pilot trial).

The real coding-agent runner is implemented and live-gated; it is runnable only when
``E2E_AB_RUN=1``, ``E2E_EXTERNAL=1``, ``E2E_TEMPLATE_BLUEPRINT_REPO`` and
``ANTHROPIC_API_KEY`` are provided. See README.md for the pre-registered endpoints, the
blinding invariant, and the honest non-claims.
"""
