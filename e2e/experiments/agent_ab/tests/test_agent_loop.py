"""The agent loop, driven by ScriptedClient (no LLM): capture, limits, and the CORRECTED blinding scan
(harness strings + advisory output only — never file reads)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import agent_loop, subject_protocol
from e2e.experiments.agent_ab.runners.model_client import ModelTurn, ScriptedClient

_SPEC = TaskSpec("T1", "add a param", ("a.cs",), "risky", ("a.cs",), "build_failure", True)
_CFG = agent_loop.RunConfig(model="m", max_tool_calls_per_run=5, max_wall_seconds_per_run=600,
                            tools=("read_file", "write_file", "list_dir", "advisory_check"))


def _setup(tmp_path, *, prompt="Do the task.", backend=None):
    backend = backend or (lambda p: {"recommended_decision": None, "risk_level": "unknown",
                                      "advisory": "ok", "detail": {}})
    return SimpleNamespace(arm="control", repo_path=tmp_path, advisory_backend=backend,
                           subject_prompt=prompt)


def _no_git(monkeypatch, files=()):
    monkeypatch.setattr(agent_loop, "_git_diff_name_only", lambda p: tuple(files))


def _tool(name, inp=None):
    return ModelTurn(tool_calls=[{"id": "1", "name": name, "input": inp or {}}], stop_reason="tool_use")


def test_end_turn_no_tools(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(text="done", stop_reason="end_turn")]), config=_CFG)
    assert r.tool_calls == () and r.task_id == "T1" and r.arm == "control"
    assert r.limit_reason == "model_stop"


def test_tool_call_captured_with_sequence(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    client = ScriptedClient([_tool("list_dir"), ModelTurn(text="done", stop_reason="end_turn")])
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=_CFG)
    assert [c.sequence for c in r.tool_calls] == [0]
    assert r.tool_calls[0].name == "list_dir"


def test_write_capability_exposes_companion_edit_file_schema():
    schemas = {item["name"]: item for item in agent_loop._build_tools_schema(_CFG.tools)}

    assert "edit_file" in schemas
    edit_input = schemas["edit_file"]["input_schema"]
    assert edit_input["required"] == ["path", "old_string", "new_string"]
    assert edit_input["properties"]["replace_all"]["type"] == "boolean"


def test_two_calls_monotonic(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    client = ScriptedClient([_tool("list_dir"), _tool("list_dir"),
                             ModelTurn(text="d", stop_reason="end_turn")])
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=_CFG)
    assert [c.sequence for c in r.tool_calls] == [0, 1]


def test_max_tool_calls_limit(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    cfg = agent_loop.RunConfig(model="m", max_tool_calls_per_run=2, tools=_CFG.tools)
    client = ScriptedClient([_tool("list_dir")] * 10 + [ModelTurn(stop_reason="end_turn")])
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=cfg)
    assert len(r.tool_calls) == 2
    assert r.limit_reason == "tool_call_limit"


def test_wall_time_timeout(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    ticks = iter([0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 10_000.0, 10_000.0])
    last = 10_000.0
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(ticks, last))
    cfg = agent_loop.RunConfig(model="m", max_wall_seconds_per_run=600, tools=_CFG.tools)
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([_tool("list_dir")] * 5), config=cfg)
    assert r.timed_out is True
    assert r.limit_reason == "wall_clock"


def test_late_turn_write_is_not_dispatched_after_wall_clock_expires(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    (tmp_path / "a.ts").write_text("export const existing = 1;\n", encoding="utf-8")
    ticks = iter([0.0, 0.0, 0.0, 601.0])
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(ticks, 601.0))
    cfg = agent_loop.RunConfig(model="m", max_wall_seconds_per_run=600, tools=_CFG.tools)
    turn = _tool("write_file", {"path": "a.ts", "content": "export const changed = 2;\n"})

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=ScriptedClient([turn]), config=cfg)

    assert r.timed_out is True
    assert r.limit_reason == "wall_clock"
    assert r.tool_calls == ()
    assert (tmp_path / "a.ts").read_text(encoding="utf-8") == "export const existing = 1;\n"


def test_tool_batch_stops_before_dispatching_next_call_after_wall_clock(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    ticks = iter([0.0, 0.0, 0.0, 1.0, 2.0, 601.0, 601.0])
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(ticks, 601.0))
    dispatched: list[str] = []
    monkeypatch.setattr(
        agent_loop,
        "_dispatch",
        lambda name, args, setup, **kwargs: dispatched.append(name) or {"ok": True},
    )
    turn = ModelTurn(
        tool_calls=[
            {"id": "1", "name": "list_dir", "input": {}},
            {"id": "2", "name": "advisory_check", "input": {}},
        ],
        stop_reason="tool_use",
    )

    result = agent_loop.run(
        _setup(tmp_path),
        _SPEC,
        0,
        client=ScriptedClient([turn]),
        config=agent_loop.RunConfig(model="m", max_wall_seconds_per_run=600, tools=_CFG.tools),
    )

    assert dispatched == ["list_dir"]
    assert result.timed_out is True
    assert result.limit_reason == "wall_clock"


def test_advisory_dispatch_receives_remaining_wall_budget(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    ticks = iter([0.0, 0.0, 100.0, 100.0, 101.0, 101.0, 101.0, 101.0])
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(ticks, 101.0))
    remaining: list[float | None] = []
    monkeypatch.setattr(
        agent_loop,
        "_dispatch",
        lambda name, args, setup, **kwargs: remaining.append(kwargs.get("timeout_seconds"))
        or {"ok": True},
    )
    turn = ModelTurn(
        tool_calls=[{"id": "1", "name": "advisory_check", "input": {}}],
        stop_reason="tool_use",
    )

    agent_loop.run(
        _setup(tmp_path),
        _SPEC,
        0,
        client=ScriptedClient([turn, ModelTurn(stop_reason="end_turn")]),
        config=agent_loop.RunConfig(model="m", max_wall_seconds_per_run=600, tools=_CFG.tools),
    )

    assert remaining == [pytest.approx(499.0)]


def test_prompt_leak_aborts_before_first_send(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    with pytest.raises(agent_loop.BlindingViolationError):
        agent_loop.run(_setup(tmp_path, prompt="This is an experiment about X."), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(stop_reason="end_turn")]), config=_CFG)


def test_advisory_output_leak_aborts(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    def leaky(_p):
        return {"recommended_decision": None, "risk_level": "unknown",
                "advisory": "generated by PEBRA", "detail": {}}
    client = ScriptedClient([_tool("advisory_check", {
        "target_file": "a.cs", "change_summary": "edit a", "proposed_patch": "diff --git a/a.cs b/a.cs",
    }),
                             ModelTurn(stop_reason="end_turn")])
    with pytest.raises(agent_loop.BlindingViolationError):
        agent_loop.run(_setup(tmp_path, backend=leaky), _SPEC, 0, client=client, config=_CFG)


def test_blinding_abort_message_contains_redacted_diagnostic(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    client = ScriptedClient([_tool("advisory_check", {
        "target_file": "a.cs", "change_summary": "edit a", "proposed_patch": "diff --git a/a.cs b/a.cs",
    }),
                             ModelTurn(stop_reason="end_turn")])

    with pytest.raises(agent_loop.BlindingViolationError) as ei:
        agent_loop.run(
            _setup(tmp_path, backend=lambda _p: {
                "recommended_decision": None, "risk_level": "unknown",
                "advisory": "generated by PEBRA", "detail": {},
            }),
            _SPEC,
            0,
            client=client,
            config=_CFG,
        )

    msg = str(ei.value)
    assert "redacted_text=" in msg
    assert "PEBRA" not in msg


def test_file_read_with_forbidden_word_does_not_abort(tmp_path, monkeypatch):
    # Corrected scan: repo content the agent reads is NOT scanned. A file containing "graph" (or even
    # "oracle") must not abort the run.
    _no_git(monkeypatch)
    (tmp_path / "Chart.cs").write_text("// draws a graph; see the Oracle DB adapter\n")
    client = ScriptedClient([_tool("read_file", {"path": "Chart.cs"}),
                             ModelTurn(text="done", stop_reason="end_turn")])
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=_CFG)
    assert r.error is None and len(r.tool_calls) == 1


def test_protocol_file_read_is_recorded(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    path = tmp_path / subject_protocol.INSTRUCTION_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text("instructions", encoding="utf-8")
    client = ScriptedClient([
        _tool("read_file", {"path": subject_protocol.INSTRUCTION_REL_PATH}),
        ModelTurn(text="done", stop_reason="end_turn"),
    ])

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=_CFG)

    assert r.protocol_file_read is True


def test_protocol_file_read_defaults_false(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(text="done")]), config=_CFG)
    assert r.protocol_file_read is False


def test_trace_sidecar_records_turns_tools_and_final_state(tmp_path, monkeypatch):
    _no_git(monkeypatch, files=("a.cs",))
    trace_path = tmp_path / "subject_trace.json"
    path = tmp_path / subject_protocol.INSTRUCTION_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text("instructions", encoding="utf-8")
    client = ScriptedClient([
        _tool("read_file", {"path": subject_protocol.INSTRUCTION_REL_PATH}),
        ModelTurn(text="done", stop_reason="end_turn", served_model="m-served"),
    ])

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=_CFG, trace_path=trace_path)

    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "agent_ab.subject_trace.v1"
    assert payload["task_id"] == "T1"
    assert payload["arm"] == "control"
    assert payload["model"] == "m"
    assert payload["final"]["protocol_file_read"] is True
    assert payload["final"]["modified_files"] == ["a.cs"]
    assert payload["final"]["served_models"] == ["m-served"]
    assert payload["turns"][0]["tool_calls"][0]["name"] == "read_file"
    assert payload["tool_calls"][0]["name"] == "read_file"
    assert payload["tool_calls"][0]["latency_seconds"] >= 0
    assert r.protocol_file_read is True


def test_write_traversal_result_is_error_run_continues(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    client = ScriptedClient([_tool("write_file", {"path": "../../evil", "content": "x"}),
                             ModelTurn(text="done", stop_reason="end_turn")])
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=_CFG)
    # a blocked/failed write is normalized to ok=False + reason (no arm-distinguishing "error" key);
    # the run continues (r.error is None).
    result = r.tool_calls[0].result
    assert result["ok"] is False and result["reason"] and r.error is None


def test_write_gate_receives_exact_attempted_content(tmp_path):
    setup = _setup(tmp_path)
    seen = {}
    setup.gate_check_backend = lambda event: seen.setdefault("event", event) or {
        "permission": "allow"
    }

    agent_loop._gated_write({"path": "a.cs", "content": "new content"}, setup)

    assert seen["event"]["tool_input"] == {
        "file_path": "a.cs",
        "content": "new content",
    }


def test_edit_file_uses_edit_gate_event_and_applies_replacement(tmp_path):
    (tmp_path / "a.cs").write_text("before old after", encoding="utf-8")
    setup = _setup(tmp_path)
    seen = {}

    def gate(event):
        seen["event"] = event
        return {"permission": "allow"}

    setup.gate_check_backend = gate
    result = agent_loop._dispatch(
        "edit_file",
        {"path": "a.cs", "old_string": "old", "new_string": "new", "replace_all": False},
        setup,
    )

    assert seen["event"]["tool_name"] == "Edit"
    assert seen["event"]["tool_input"] == {
        "file_path": "a.cs",
        "old_string": "old",
        "new_string": "new",
        "replace_all": False,
    }
    assert result == {"ok": True, "blocked": False, "reason": None}
    assert (tmp_path / "a.cs").read_text(encoding="utf-8") == "before new after"


def test_edit_file_gate_denial_is_arm_blind_and_does_not_mutate(tmp_path):
    (tmp_path / "a.cs").write_text("old", encoding="utf-8")
    setup = _setup(tmp_path)
    setup.gate_check_backend = lambda _event: {"permission": "deny", "reason": "Revise first."}

    result = agent_loop._dispatch(
        "edit_file", {"path": "a.cs", "old_string": "old", "new_string": "new"}, setup
    )

    assert result == {"ok": False, "blocked": True, "reason": "Revise first."}
    assert (tmp_path / "a.cs").read_text(encoding="utf-8") == "old"


def test_apply_patch_uses_atomic_gate_event_and_applies_all_files(tmp_path):
    (tmp_path / "a.ts").write_text("const a = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("const b = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
--- a/a.ts
+++ b/a.ts
@@ -1 +1 @@
-const a = 1;
+const a = 2;
diff --git a/b.ts b/b.ts
--- a/b.ts
+++ b/b.ts
@@ -1 +1 @@
-const b = 1;
+const b = 2;
"""
    setup = _setup(tmp_path)
    seen = {}
    def gate(event):
        seen["event"] = event
        return {"permission": "allow"}
    setup.gate_check_backend = gate

    result = agent_loop._dispatch("apply_patch", {"patch": patch}, setup)

    assert seen["event"]["tool_name"] == "apply_patch"
    assert seen["event"]["tool_input"] == {"command": patch}
    assert result == {"ok": True, "blocked": False, "reason": None}
    assert (tmp_path / "a.ts").read_text(encoding="utf-8") == "const a = 2;\n"
    assert (tmp_path / "b.ts").read_text(encoding="utf-8") == "const b = 2;\n"


def test_edit_file_reason_is_scanned_for_blinding_leaks(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    (tmp_path / "a.cs").write_text("old", encoding="utf-8")
    setup = _setup(tmp_path)
    setup.gate_check_backend = lambda _event: {
        "permission": "deny", "reason": "generated by PEBRA"
    }
    client = ScriptedClient([
        _tool("edit_file", {"path": "a.cs", "old_string": "old", "new_string": "new"}),
    ])

    with pytest.raises(agent_loop.BlindingViolationError):
        agent_loop.run(setup, _SPEC, 0, client=client, config=_CFG)


def test_live_client_error_is_captured_into_result_not_crash(tmp_path, monkeypatch):
    # a live client/API failure (auth/rate/network) must be captured into SubjectResult.error and the
    # run returned as errored — NOT crash the batch (one bad run shouldn't abort the whole pilot).
    _no_git(monkeypatch)

    class FailingClient:
        def send(self, *_a, **_k):
            raise RuntimeError("model call failed")

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=FailingClient(), config=_CFG)
    assert r.error is not None and "model call failed" in r.error


def test_not_implemented_still_surfaces_as_programmer_error(tmp_path, monkeypatch):
    # NotImplementedError is a programmer error, not a run-time API failure: it must propagate, not be
    # masked into an errored run.
    _no_git(monkeypatch)

    class BrokenClient:
        def send(self, *_a, **_k):
            raise NotImplementedError("unfinished path")

    with pytest.raises(NotImplementedError, match="unfinished path"):
        agent_loop.run(_setup(tmp_path), _SPEC, 0, client=BrokenClient(), config=_CFG)


def test_diff_capture_includes_untracked_files(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "new.cs").write_text("new")
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(text="done")]), config=_CFG)
    assert r.modified_files == ("new.cs",)


def test_diff_capture_filters_harness_artifacts(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / ".gitignore").write_text("*\n!.gitignore\n")
    (tmp_path / ".pebra").mkdir()
    (tmp_path / ".pebra" / "state.json").write_text("{}")
    (tmp_path / ".agent-instructions").mkdir()
    (tmp_path / ".agent-instructions" / "edit_protocol.md").write_text("instructions")
    (tmp_path / "new.cs").write_text("new")

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(text="done")]), config=_CFG)

    assert r.modified_files == ("new.cs",)


def test_diff_capture_filters_pebra_only_gitignore_change(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("bin/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    with (tmp_path / ".gitignore").open("a", encoding="utf-8") as fh:
        fh.write("\n.pebra/\n")
    (tmp_path / "new.cs").write_text("new")

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(text="done")]), config=_CFG)

    assert r.modified_files == ("new.cs",)


def test_diff_capture_filters_staged_pebra_only_gitignore_change(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("bin/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    with (tmp_path / ".gitignore").open("a", encoding="utf-8") as fh:
        fh.write("\n.pebra/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    (tmp_path / "new.cs").write_text("new")

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(text="done")]), config=_CFG)

    assert r.modified_files == ("new.cs",)


def test_diff_capture_keeps_real_gitignore_edits(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("bin/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    with (tmp_path / ".gitignore").open("a", encoding="utf-8") as fh:
        fh.write("\ndist/\n")

    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([ModelTurn(text="done")]), config=_CFG)

    assert r.modified_files == (".gitignore",)


def test_max_tokens_tool_use_is_executed_not_dropped(tmp_path, monkeypatch):
    _no_git(monkeypatch, files=("a.cs",))
    turn = ModelTurn(
        tool_calls=[{"id": "1", "name": "write_file", "input": {"path": "a.cs", "content": "x"}}],
        stop_reason="max_tokens",
    )
    r = agent_loop.run(_setup(tmp_path), _SPEC, 0,
                       client=ScriptedClient([turn, ModelTurn(text="done")]), config=_CFG)
    assert (tmp_path / "a.cs").read_text() == "x"
    assert r.tool_calls[0].name == "write_file"
    assert r.final_stop_reason == "end_turn"
    assert r.turn_count == 2
