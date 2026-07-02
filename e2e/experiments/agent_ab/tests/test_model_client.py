"""ScriptedClient replays deterministically; the Anthropic response->ModelTurn mapping is pure and
unit-tested with a fake response (no network, no SDK); SDK import stays lazy."""

from __future__ import annotations

import pytest

from e2e.experiments.agent_ab.runners import model_client as mc


def test_scripted_replays_in_order():
    t0 = mc.ModelTurn(text="a", stop_reason="end_turn")
    t1 = mc.ModelTurn(text="b", tool_calls=[{"id": "1", "name": "read_file", "input": {}}],
                      stop_reason="tool_use")
    c = mc.ScriptedClient([t0, t1])
    assert c.send([], [], "sys", max_tokens=10) is t0
    assert c.send([], [], "sys", max_tokens=10) is t1
    assert len(c.calls) == 2 and c.calls[0]["system"] == "sys"


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
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


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


def test_response_to_turn_joins_text_and_defaults_empty():
    resp = _Resp(content=[_Block(type="text", text="a"), _Block(type="text", text="b")],
                 stop_reason="end_turn")
    turn = mc._response_to_turn(resp)
    assert turn.text == "a\nb" and turn.tool_calls == []


def test_response_to_turn_no_content_is_none_text_and_stop_fallback():
    turn = mc._response_to_turn(_Resp(content=[], stop_reason=None))
    assert turn.text is None and turn.tool_calls == [] and turn.stop_reason == "end_turn"


def test_anthropic_client_ctor_builds_no_sdk_client():
    # constructing the client must not import the SDK or open a connection (lazy on first send)
    client = mc.AnthropicClient(model="claude-sonnet-4-6", api_key="sk-test")
    assert client._client is None


def test_module_imports_without_sdk():
    # importing model_client must not require anthropic (the import is lazy inside the live path)
    import importlib
    assert importlib.import_module("e2e.experiments.agent_ab.runners.model_client") is mc
