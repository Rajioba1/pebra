"""ScriptedClient replays deterministically; AnthropicClient.send is a Phase-G stop; SDK import lazy."""

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


def test_anthropic_send_is_phase_g_stop():
    client = mc.AnthropicClient(model="claude-sonnet-4-6", api_key="sk-test")
    with pytest.raises(NotImplementedError, match="Phase G"):
        client.send([], [], "sys", max_tokens=10)


def test_module_imports_without_sdk():
    # importing model_client must not require anthropic (the import is lazy inside the live path)
    import importlib
    assert importlib.import_module("e2e.experiments.agent_ab.runners.model_client") is mc
