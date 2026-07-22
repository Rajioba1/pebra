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

import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelTurn:
    """One model response: assistant text, any tool-use requests, and why it stopped."""

    text: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{"id","name","input"}]
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use" | "max_tokens"
    served_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    # Exact provider blocks must be replayed after a thinking-mode tool call. Reconstructing only
    # text/tool_use drops DeepSeek's signed reasoning block and makes the next request invalid.
    provider_content: list[dict[str, Any]] = field(default_factory=list)


class ModelClient(Protocol):
    def send(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        *,
        max_tokens: int,
        timeout_seconds: float | None = None,
    ) -> ModelTurn: ...


class ScriptExhausted(RuntimeError):
    """A ScriptedClient was asked for more turns than were scripted."""


class ScriptedClient:
    """Deterministic test double: replays a fixed list of ModelTurns in order. No network, no SDK."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)
        self._i = 0
        self.calls: list[dict[str, Any]] = []  # captured (messages,tools,system) per send, for assertions

    def send(self, messages, tools, system, *, max_tokens, timeout_seconds=None) -> ModelTurn:
        self.calls.append({"messages": messages, "tools": tools, "system": system,
                           "max_tokens": max_tokens, "timeout_seconds": timeout_seconds})
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
    provider_content: list[dict[str, Any]] = []
    for block in getattr(resp, "content", None) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            block_text = getattr(block, "text", "") or ""
            text_parts.append(block_text)
            provider_content.append({"type": "text", "text": block_text})
        elif btype == "tool_use":
            tool_call = {
                "id": getattr(block, "id", None),
                "name": getattr(block, "name", None),
                "input": getattr(block, "input", None) or {},
            }
            tool_calls.append(tool_call)
            provider_content.append({"type": "tool_use", **tool_call})
        elif btype == "thinking":
            provider_content.append({
                "type": "thinking",
                "thinking": getattr(block, "thinking", "") or "",
                "signature": getattr(block, "signature", "") or "",
            })
        elif btype == "redacted_thinking":
            provider_content.append({
                "type": "redacted_thinking",
                "data": getattr(block, "data", "") or "",
            })
    text = "\n".join(p for p in text_parts if p) or None
    usage = getattr(resp, "usage", None)
    return ModelTurn(text=text, tool_calls=tool_calls,
                     stop_reason=getattr(resp, "stop_reason", None) or "end_turn",
                     served_model=getattr(resp, "model", None),
                     input_tokens=_usage_count(usage, "input_tokens"),
                     output_tokens=_usage_count(usage, "output_tokens"),
                     cache_read_tokens=_usage_count(usage, "cache_read_input_tokens"),
                     cache_write_tokens=_usage_count(usage, "cache_creation_input_tokens"),
                     provider_content=provider_content)


def _usage_count(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None)
    return value if type(value) is int and value >= 0 else None


class AnthropicClient:
    """Real subject client (Phase G). Reachable only behind the run gate. Lazily constructs the SDK
    client on first ``send``, then maps each response via the pure ``_response_to_turn``."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        transient_retries: int = 2,
        base_url: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._transient_retries = transient_retries
        self._base_url = base_url
        self._thinking_enabled = thinking_enabled
        self._client: Any = None  # lazily constructed on first send (needs the SDK + a real key)

    def send(  # pragma: no cover - live only
        self, messages, tools, system, *, max_tokens, timeout_seconds=None
    ) -> ModelTurn:
        if self._client is None:
            # The SDK retries requests internally by default, with a fresh per-attempt timeout. Disable
            # that hidden loop so this class's deadline-aware retry loop is the sole retry authority.
            # DeepSeek uses this same Anthropic-compatible client, so the rule covers both providers.
            kwargs = {"api_key": self._api_key, "max_retries": 0}
            if self._base_url is not None:
                kwargs["base_url"] = self._base_url
            self._client = _import_anthropic().Anthropic(**kwargs)
        attempts = self._transient_retries + 1
        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        for attempt in range(attempts):
            try:
                request: dict[str, Any] = {
                    "model": self._model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "tools": tools,
                    "messages": messages,
                }
                if self._thinking_enabled is not None:
                    request["thinking"] = {
                        "type": "enabled" if self._thinking_enabled else "disabled"
                    }
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("model request exhausted its shared run budget")
                    request["timeout"] = remaining
                resp = self._client.messages.create(**request)
                break
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                transient = status == 429 or (isinstance(status, int) and 500 <= status < 600)
                if not transient or attempt == attempts - 1:
                    raise
        return _response_to_turn(resp)
