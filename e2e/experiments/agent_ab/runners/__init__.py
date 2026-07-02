"""Runner scaffold. Prepares blinded, isolated arms and enters the agent loop only behind the run gate.
The live model client still hard-stops at ``AnthropicClient.send`` until Phase G is ratified."""
