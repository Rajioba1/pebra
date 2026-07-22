"""ScriptedClient replays deterministically; the Anthropic response->ModelTurn mapping is pure and
unit-tested with a fake response (no network, no SDK); SDK import stays lazy."""

from __future__ import annotations

import pytest

from e2e.experiments.agent_ab.runners import agent_loop
from e2e.experiments.agent_ab.runners import model_client as mc


def test_scripted_replays_in_order():
    t0 = mc.ModelTurn(text="a", stop_reason="end_turn")
    t1 = mc.ModelTurn(text="b", tool_calls=[{"id": "1", "name": "read_file", "input": {}}],
                      stop_reason="tool_use")
    c = mc.ScriptedClient([t0, t1])
    assert c.send([], [], "sys", max_tokens=10, timeout_seconds=7.5) is t0
    assert c.send([], [], "sys", max_tokens=10) is t1
    assert len(c.calls) == 2 and c.calls[0]["system"] == "sys"
    assert c.calls[0]["timeout_seconds"] == 7.5


def test_scripted_exhaustion_raises():
    c = mc.ScriptedClient([mc.ModelTurn(text="only")])
    c.send([], [], "s", max_tokens=1)
    with pytest.raises(mc.ScriptExhausted):
        c.send([], [], "s", max_tokens=1)


def test_model_turn_defaults():
    t = mc.ModelTurn()
    assert t.text is None and t.tool_calls == [] and t.stop_reason == "end_turn"


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason, *, usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.model = "claude-haiku-4-5-20251001"
        self.usage = usage


def test_response_to_turn_maps_text_and_tool_use():
    resp = _Resp(
        content=[
            _Block(type="text", text="thinking"),
            _Block(type="tool_use", id="tu_1", name="advisory_check", input={"target_file": "a.cs"}),
        ],
        stop_reason="tool_use",
    )
    turn = mc._response_to_turn(resp)
    assert turn.text == "thinking"
    assert turn.tool_calls == [{"id": "tu_1", "name": "advisory_check", "input": {"target_file": "a.cs"}}]
    assert turn.stop_reason == "tool_use"
    assert turn.served_model == "claude-haiku-4-5-20251001"


def test_response_to_turn_preserves_thinking_block_for_tool_call_replay():
    resp = _Resp(
        content=[
            _Block(type="thinking", thinking="private reasoning", signature="signed"),
            _Block(type="tool_use", id="tu_1", name="read_file", input={"path": "a.ts"}),
        ],
        stop_reason="tool_use",
    )

    turn = mc._response_to_turn(resp)

    assert turn.provider_content == [
        {"type": "thinking", "thinking": "private reasoning", "signature": "signed"},
        {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "a.ts"}},
    ]
    assert agent_loop._turn_to_content(turn) == turn.provider_content


def test_response_to_turn_joins_text_and_defaults_empty():
    resp = _Resp(content=[_Block(type="text", text="a"), _Block(type="text", text="b")],
                 stop_reason="end_turn")
    turn = mc._response_to_turn(resp)
    assert turn.text == "a\nb" and turn.tool_calls == []


def test_response_to_turn_no_content_is_none_text_and_stop_fallback():
    turn = mc._response_to_turn(_Resp(content=[], stop_reason=None))
    assert turn.text is None and turn.tool_calls == [] and turn.stop_reason == "end_turn"


def test_response_to_turn_normalizes_provider_token_usage():
    turn = mc._response_to_turn(_Resp(
        content=[_Block(type="text", text="ok")],
        stop_reason="end_turn",
        usage=_Block(
            input_tokens=123,
            output_tokens=17,
            cache_read_input_tokens=41,
            cache_creation_input_tokens=9,
        ),
    ))

    assert turn.input_tokens == 123
    assert turn.output_tokens == 17
    assert turn.cache_read_tokens == 41
    assert turn.cache_write_tokens == 9


def test_response_to_turn_preserves_missing_or_invalid_usage_as_unavailable():
    missing = mc._response_to_turn(_Resp(content=[], stop_reason="end_turn"))
    invalid = mc._response_to_turn(_Resp(
        content=[], stop_reason="end_turn",
        usage=_Block(input_tokens=True, output_tokens=-1),
    ))

    assert missing.input_tokens is None and missing.output_tokens is None
    assert invalid.input_tokens is None and invalid.output_tokens is None


def test_anthropic_client_ctor_builds_no_sdk_client():
    # constructing the client must not import the SDK or open a connection (lazy on first send)
    client = mc.AnthropicClient(model="claude-sonnet-4-6", api_key="sk-test")
    assert client._client is None


def test_anthropic_client_passes_base_url_to_sdk(monkeypatch):
    captured = {}

    class _Messages:
        def create(self, **_kwargs):
            return _Resp(content=[_Block(type="text", text="ok")], stop_reason="end_turn")

    class _Anthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = _Messages()

    monkeypatch.setattr(mc, "_import_anthropic", lambda: type("SDK", (), {"Anthropic": _Anthropic}))

    client = mc.AnthropicClient(
        model="deepseek-v4-flash",
        api_key="sk-test",
        base_url="https://api.deepseek.com/anthropic",
    )
    client.send([], [], "system", max_tokens=10)

    assert captured == {
        "api_key": "sk-test",
        "base_url": "https://api.deepseek.com/anthropic",
        "max_retries": 0,
    }


def test_anthropic_client_can_explicitly_disable_thinking_for_diagnostic_run(monkeypatch):
    request = {}

    class _Messages:
        def create(self, **kwargs):
            request.update(kwargs)
            return _Resp(content=[_Block(type="text", text="ok")], stop_reason="end_turn")

    class _Anthropic:
        def __init__(self, **_kwargs):
            self.messages = _Messages()

    monkeypatch.setattr(mc, "_import_anthropic", lambda: type("SDK", (), {"Anthropic": _Anthropic}))
    client = mc.AnthropicClient(
        model="deepseek-v4-pro", api_key="sk-test", thinking_enabled=False
    )

    client.send([], [], "system", max_tokens=10, timeout_seconds=7.5)

    assert request["thinking"] == {"type": "disabled"}
    assert 0 < request["timeout"] <= 7.5


def test_anthropic_client_retries_transient_errors(monkeypatch):
    constructed = {}

    class _TransientError(Exception):
        status_code = 429

    class _Messages:
        def __init__(self):
            self.calls = 0
            self.timeouts = []

        def create(self, **kwargs):
            self.calls += 1
            self.timeouts.append(kwargs.get("timeout"))
            if self.calls == 1:
                raise _TransientError("rate limited")
            return _Resp(content=[_Block(type="text", text="ok")], stop_reason="end_turn")

    class _Anthropic:
        def __init__(self, api_key, max_retries):
            constructed.update({"api_key": api_key, "max_retries": max_retries})
            self.messages = _Messages()

    monkeypatch.setattr(mc, "_import_anthropic", lambda: type("SDK", (), {"Anthropic": _Anthropic}))
    clock = iter((0.0, 0.0, 3.0))
    monkeypatch.setattr(mc.time, "monotonic", lambda: next(clock, 3.0))

    client = mc.AnthropicClient(model="claude-sonnet-4-6", api_key="sk-test")
    turn = client.send([], [], "system", max_tokens=10, timeout_seconds=10)

    assert turn.text == "ok"
    assert constructed == {"api_key": "sk-test", "max_retries": 0}
    assert client._client.messages.calls == 2
    assert client._client.messages.timeouts == [10.0, 7.0]


@pytest.mark.parametrize("status_code", (429, 500))
def test_anthropic_client_zero_transient_retries_attempts_once(monkeypatch, status_code):
    class _TransientError(Exception):
        pass

    class _Messages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            error = _TransientError("transient provider failure")
            error.status_code = status_code
            raise error

    class _Anthropic:
        def __init__(self, api_key, max_retries):
            assert max_retries == 0
            self.messages = _Messages()

    monkeypatch.setattr(mc, "_import_anthropic", lambda: type("SDK", (), {"Anthropic": _Anthropic}))

    client = mc.AnthropicClient(
        model="deepseek-v4-pro", api_key="sk-test", transient_retries=0
    )
    with pytest.raises(_TransientError):
        client.send([], [], "system", max_tokens=10)

    assert client._client.messages.calls == 1


def test_anthropic_client_does_not_retry_non_transient_errors(monkeypatch):
    class _AuthError(Exception):
        status_code = 401

    class _Messages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            raise _AuthError("bad key")

    class _Anthropic:
        def __init__(self, api_key, max_retries):
            assert max_retries == 0
            self.messages = _Messages()

    monkeypatch.setattr(mc, "_import_anthropic", lambda: type("SDK", (), {"Anthropic": _Anthropic}))

    client = mc.AnthropicClient(model="claude-sonnet-4-6", api_key="sk-test")
    with pytest.raises(_AuthError):
        client.send([], [], "system", max_tokens=10)
    assert client._client.messages.calls == 1


def test_module_imports_without_sdk():
    # importing model_client must not require anthropic (the import is lazy inside the live path)
    import importlib
    assert importlib.import_module("e2e.experiments.agent_ab.runners.model_client") is mc
