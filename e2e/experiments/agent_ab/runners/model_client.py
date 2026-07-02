"""Model-client abstraction for the subject agent — the seam that keeps the loop testable.

The ONLY stochastic part of the runner is a model call. This module hides it behind a ``ModelClient``
Protocol so ``agent_loop`` can be driven deterministically by ``ScriptedClient`` (no network, no tokens)
in unit tests, and by ``AnthropicClient`` (real, Phase G) in a ratified live run.

``AnthropicClient.send`` is LIVE (Phase G): it calls the Anthropic Messages API. It is reachable ONLY
behind the run gate (E2E_AB_RUN + E2E_EXTERNAL + ANTHROPIC_API_KEY) enforced in run_pair/orchestrator,
so the deterministic test suite never touches it. ``anthropic`` is imported lazily inside the client
only, so importing this module (and running the whole test suite) needs no SDK installed. The pure
Message->ModelTurn mapping (``_response_to_turn``) is factored out and unit-tested with a fake response
object — no network, no key. No pebra import.
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


def _response_to_turn(resp: Any) -> ModelTurn:
    """Pure map of an Anthropic ``Message`` -> ``ModelTurn``. Kept separate from the network call so the
    mapping is unit-tested with a fake response (an object with ``.content`` blocks + ``.stop_reason``).

    ``content`` is a list of blocks; ``type == "text"`` -> assistant text, ``type == "tool_use"`` ->
    a tool call ``{"id","name","input"}``. Text blocks are joined; stop_reason falls back to end_turn."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in getattr(resp, "content", None) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            tool_calls.append({
                "id": getattr(block, "id", None),
                "name": getattr(block, "name", None),
                "input": getattr(block, "input", None) or {},
            })
    text = "\n".join(p for p in text_parts if p) or None
    return ModelTurn(text=text, tool_calls=tool_calls,
                     stop_reason=getattr(resp, "stop_reason", None) or "end_turn")


class AnthropicClient:
    """Real subject client (Phase G). Reachable only behind the run gate. Lazily constructs the SDK
    client on first ``send``, then maps each response via the pure ``_response_to_turn``."""

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._api_key = api_key
        self._client: Any = None  # lazily constructed on first send (needs the SDK + a real key)

    def send(self, messages, tools, system, *, max_tokens) -> ModelTurn:  # pragma: no cover - live only
        if self._client is None:
            self._client = _import_anthropic().Anthropic(api_key=self._api_key)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        return _response_to_turn(resp)
