"""Model-client abstraction for the subject agent — the seam that keeps the loop testable.

The ONLY stochastic part of the runner is a model call. This module hides it behind a ``ModelClient``
Protocol so ``agent_loop`` can be driven deterministically by ``ScriptedClient`` (no network, no tokens)
in unit tests, and by ``AnthropicClient`` (real, Phase G) in a ratified live run.

``AnthropicClient.send`` is a STOP: it raises NotImplementedError. Enabling it is the ratified live
slice. ``anthropic`` is imported lazily inside ``AnthropicClient`` only, so importing this module (and
running the whole test suite) needs no SDK installed. No pebra import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelTurn:
    """One model response: assistant text, any tool-use requests, and why it stopped."""

    text: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{"id","name","input"}]
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use" | "max_tokens"


class ModelClient(Protocol):
    def send(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        *,
        max_tokens: int,
    ) -> ModelTurn: ...


class ScriptExhausted(RuntimeError):
    """A ScriptedClient was asked for more turns than were scripted."""


class ScriptedClient:
    """Deterministic test double: replays a fixed list of ModelTurns in order. No network, no SDK."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)
        self._i = 0
        self.calls: list[dict[str, Any]] = []  # captured (messages,tools,system) per send, for assertions

    def send(self, messages, tools, system, *, max_tokens) -> ModelTurn:
        self.calls.append({"messages": messages, "tools": tools, "system": system,
                           "max_tokens": max_tokens})
        if self._i >= len(self._turns):
            raise ScriptExhausted(f"ScriptedClient exhausted after {len(self._turns)} turn(s)")
        turn = self._turns[self._i]
        self._i += 1
        return turn


def _import_anthropic():  # pragma: no cover - exercised only in the live Phase-G run
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:  # honest install hint; SDK is an optional dep
        raise ImportError("anthropic SDK required for the live A/B run: pip install anthropic") from exc
    return anthropic


class AnthropicClient:
    """Real subject client. STOPPED: ``send`` raises until the live slice is ratified."""

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._api_key = api_key
        # NOTE: the SDK client is intentionally NOT constructed here while send() is a stop.
        # The live slice will lazily construct it via _import_anthropic().

    def send(self, messages, tools, system, *, max_tokens) -> ModelTurn:
        raise NotImplementedError("Phase G — live client; ratify before enabling")
