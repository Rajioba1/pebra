"""Runner scaffold. Prepares blinded, isolated arms and enters the agent loop only behind the run gate.
``AnthropicClient.send`` is now implemented (Phase G), so the ``NotImplementedError`` stop is GONE:
the fail-closed run gate (E2E_AB_RUN + E2E_EXTERNAL + ANTHROPIC_API_KEY) is the SOLE guard against an
accidental run, and nothing in-tree sets those vars."""
