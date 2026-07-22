"""The agent loop, driven by ScriptedClient (no LLM): capture, limits, and the CORRECTED blinding scan
(harness strings + advisory output only — never file reads)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from e2e.experiments.agent_ab.metrics import oracle
from e2e.experiments.agent_ab.models import TaskSpec, ToolCallRecord
from e2e.experiments.agent_ab.runners import agent_loop, subject_protocol
from e2e.experiments.agent_ab.runners.model_client import ModelTurn, ScriptedClient
from e2e.experiments.agent_ab.tools import advisory_contract

_SPEC = TaskSpec("T1", "add a param", ("a.cs",), "risky", ("a.cs",), "build_failure", True)
_CFG = agent_loop.RunConfig(model="m", max_tool_calls_per_run=5, max_wall_seconds_per_run=600,
                            tools=("read_file", "write_file", "list_dir", "advisory_check"))


def _setup(tmp_path, *, prompt="Do the task.", backend=None, approval_backend=None):
    backend = backend or (lambda p: {"recommended_decision": None, "risk_level": "unknown",
                                      "advisory": "ok", "detail": {}})
    approval_backend = approval_backend or (lambda _payload: {
        "status": "unavailable", "approval_id": None, "message": "No approval is pending.",
    })
    return SimpleNamespace(
        arm="control", repo_path=tmp_path, advisory_backend=backend,
        approval_backend=approval_backend, subject_prompt=prompt,
        candidate_patches={},
    )


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


def test_subject_trace_keeps_host_only_advisory_failure_and_terminal_reason(
    tmp_path, monkeypatch
):
    _no_git(monkeypatch)
    setup = _setup(tmp_path)
    setup.telemetry = SimpleNamespace(real_advisory_failures=[{
        "category": "insufficient_wall_budget",
        "attempted": False,
        "remaining_budget_seconds": 5.9,
    }])
    trace_path = tmp_path / "subject_trace.json"

    agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient([ModelTurn(text="done", stop_reason="end_turn")]),
        config=_CFG,
        trace_path=trace_path,
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    assert trace["final"]["reason"] == "model_stop"
    assert trace["final"]["real_advisory_failures"] == [
        {
            "category": "insufficient_wall_budget",
            "attempted": False,
            "remaining_budget_seconds": 5.9,
        }
    ]


def test_subject_result_and_trace_keep_context_receipts_host_only(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    setup = _setup(tmp_path)
    receipt = {
        "source": "graph",
        "repo_head": "b" * 40,
        "graph_scope_digest": "a" * 64,
        "query": "helper",
        "requested_files": ["src/a.ts"],
        "returned_files": ["src/b.ts"],
        "truncated": False,
        "duration_seconds": 0.1,
        "cache_hit": False,
        "status": "available",
    }
    setup.telemetry = SimpleNamespace(
        real_advisory_failures=[], repository_context_receipts=[]
    )

    def context_backend(_payload):
        setup.telemetry.repository_context_receipts.append(receipt)
        return {
            "status": "available",
            "context": "helper source",
            "related_files": ["src/b.ts"],
            "related_tests": [],
            "warnings": [],
            "truncated": False,
        }

    setup.repository_context_backend = context_backend
    trace_path = tmp_path / "subject_trace.json"
    result = agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient(
            [
                _tool("repository_context", {"query": "helper"}),
                ModelTurn(text="done", stop_reason="end_turn"),
            ]
        ),
        config=agent_loop.RunConfig(model="m", tools=("repository_context",)),
        trace_path=trace_path,
    )

    assert result.repository_context_receipts == (receipt,)
    serialized_call = json.dumps(result.tool_calls[0].result)
    assert "graph_scope_digest" not in serialized_call
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["final"]["repository_context_receipts"] == [receipt]


def test_run_records_total_and_whole_turn_understand_usage(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    setup = _setup(tmp_path)
    setup.telemetry = SimpleNamespace(real_advisory_failures=[], repository_context_receipts=[])
    setup.repository_context_backend = lambda _payload: {
        "status": "available", "context": "current source", "related_files": [],
        "related_tests": [], "warnings": [], "truncated": False,
    }
    turns = [
        ModelTurn(
            tool_calls=[{"id": "1", "name": "repository_context", "input": {"query": "a"}}],
            stop_reason="tool_use", input_tokens=10, output_tokens=2,
        ),
        ModelTurn(text="done", stop_reason="end_turn", input_tokens=20, output_tokens=3),
    ]
    trace_path = tmp_path / "subject_trace.json"

    result = agent_loop.run(
        setup, _SPEC, 0, client=ScriptedClient(turns),
        config=agent_loop.RunConfig(model="m", tools=("repository_context",)),
        trace_path=trace_path,
    )

    assert result.token_usage["input_tokens"] == 30
    assert result.token_usage["output_tokens"] == 5
    assert result.understand_turn_usage["turn_count"] == 2
    assert result.understand_turn_usage["input_tokens"] == 30
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["token_usage"] == result.token_usage
    assert trace["understand_turn_usage"] == result.understand_turn_usage
    assert all("usage" in turn for turn in trace["turns"])


@pytest.mark.parametrize("reserve", (30, -1, float("nan"), float("inf"), True, "5"))
def test_run_config_rejects_invalid_reserve(reserve) -> None:
    with pytest.raises(ValueError, match="apply_verify_reserve_seconds"):
        agent_loop.RunConfig(
            model="m", max_wall_seconds_per_run=30, apply_verify_reserve_seconds=reserve,
        )


def test_model_does_not_start_inside_apply_verify_reserve(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    client = ScriptedClient([ModelTurn(text="must not be served")])
    clock = iter((0.0, 6.0))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 6.0))

    result = agent_loop.run(
        _setup(tmp_path), _SPEC, 0, client=client,
        config=agent_loop.RunConfig(
            model="m", max_wall_seconds_per_run=10, apply_verify_reserve_seconds=5,
        ),
    )

    assert client.calls == []
    assert result.limit_reason == "closeout_budget_reserved"


def test_model_timeout_excludes_reserved_closeout_budget(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    client = ScriptedClient([ModelTurn(text="done")])

    agent_loop.run(
        _setup(tmp_path), _SPEC, 0, client=client,
        config=agent_loop.RunConfig(
            model="m", max_wall_seconds_per_run=10, apply_verify_reserve_seconds=5,
        ),
    )

    assert len(client.calls) == 1
    assert 0 < client.calls[0]["timeout_seconds"] <= 5


def test_advisory_does_not_start_inside_apply_verify_reserve(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    calls = []
    setup = _setup(tmp_path, backend=lambda payload, **kwargs: calls.append((payload, kwargs)) or {})
    clock = iter((0.0, 0.0, 6.0, 6.0, 6.0, 6.0))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 6.0))

    result = agent_loop.run(
        setup, _SPEC, 0,
        client=ScriptedClient([_tool("advisory_check", {"target_file": "a.cs"})]),
        config=agent_loop.RunConfig(
            model="m", max_wall_seconds_per_run=10, apply_verify_reserve_seconds=5,
            tools=("advisory_check",),
        ),
    )

    assert calls == []
    assert result.limit_reason == "advisory_budget_exhausted"


def test_advisory_timeout_excludes_reserved_closeout_budget(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    timeouts = []
    setup = _setup(
        tmp_path,
        backend=lambda _payload, timeout_seconds=None: timeouts.append(timeout_seconds) or {
            "recommended_decision": "proceed", "risk_level": "low", "advisory": "ok", "detail": {},
        },
    )
    clock = iter((0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 2.0))

    agent_loop.run(
        setup, _SPEC, 0,
        client=ScriptedClient([_tool("advisory_check", {
            "target_file": "a.cs", "change_summary": "change a",
            "proposed_patch": "diff --git a/a.cs b/a.cs\n",
        }), ModelTurn(text="done")]),
        config=agent_loop.RunConfig(
            model="m", max_wall_seconds_per_run=20, apply_verify_reserve_seconds=5,
            tools=("advisory_check",),
        ),
    )

    assert len(timeouts) == 1
    assert 0 < timeouts[0] <= 14.0


def test_repository_context_does_not_start_inside_apply_verify_reserve(
    tmp_path, monkeypatch
):
    _no_git(monkeypatch)
    calls = []
    setup = _setup(tmp_path)
    setup.telemetry = SimpleNamespace(
        real_advisory_failures=[], repository_context_receipts=[]
    )
    setup.repository_context_backend = lambda payload, **kwargs: calls.append(
        (payload, kwargs)
    ) or {"status": "available", "context": "must not run"}
    clock = iter((0.0, 0.0, 6.0, 6.0, 6.0, 6.0))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 6.0))

    result = agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient([_tool("repository_context", {"query": "helper"})]),
        config=agent_loop.RunConfig(
            model="m",
            max_wall_seconds_per_run=10,
            apply_verify_reserve_seconds=5,
            tools=("repository_context",),
        ),
    )

    assert calls == []
    assert result.limit_reason == "closeout_budget_reserved"


@pytest.mark.parametrize("tool_name", ("run_build", "run_tests"))
def test_build_and_tests_do_not_start_inside_apply_verify_reserve(
    tool_name, tmp_path, monkeypatch
):
    _no_git(monkeypatch)
    calls = []

    class Backend:
        def run_build(self, *_args, **_kwargs):
            calls.append("run_build")

        def run_tests(self, *_args, **_kwargs):
            calls.append("run_tests")

    setup = _setup(tmp_path)
    setup.build_backend = Backend()
    setup.build_solution = ""
    setup.spec = _SPEC
    clock = iter((0.0, 0.0, 6.0, 6.0, 6.0, 6.0))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 6.0))

    result = agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient([_tool(tool_name)]),
        config=agent_loop.RunConfig(
            model="m",
            max_wall_seconds_per_run=10,
            apply_verify_reserve_seconds=5,
            tools=(tool_name,),
        ),
    )

    assert calls == []
    assert result.limit_reason == "closeout_budget_reserved"


def test_ordinary_patch_does_not_start_inside_apply_verify_reserve(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    target = tmp_path / "a.ts"
    target.write_text("const a = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
--- a/a.ts
+++ b/a.ts
@@ -1 +1 @@
-const a = 1;
+const a = 2;
"""
    setup = _setup(tmp_path)
    setup.gate_check_backend = lambda _event: {"permission": "allow"}
    clock = iter((0.0, 0.0, 6.0, 6.0, 6.0, 6.0))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 6.0))

    result = agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient([_tool("apply_patch", {"patch": patch})]),
        config=agent_loop.RunConfig(
            model="m",
            max_wall_seconds_per_run=10,
            apply_verify_reserve_seconds=5,
            tools=("apply_patch",),
        ),
    )

    assert target.read_text(encoding="utf-8") == "const a = 1;\n"
    assert result.limit_reason == "closeout_budget_reserved"


def test_exact_candidate_application_uses_only_its_half_of_closeout_reserve(
    tmp_path, monkeypatch
):
    _no_git(monkeypatch, files=("a.ts",))
    patch = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n"
    patch_id = advisory_contract.candidate_patch_id(patch)
    setup = _setup(tmp_path)
    setup.candidate_patches[patch_id] = patch
    setup.candidate_assessments = {patch_id: "asm_7"}
    calls = []
    setup.apply_candidate_backend = lambda assessment_id, **kwargs: calls.append(
        (assessment_id, kwargs)
    ) or {"status": "applied", "changed_files": ["a.ts"]}
    setup.write_applied_backend = lambda _payload: None
    clock = iter((0.0, 0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 5.0))

    result = agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient([_tool("apply_patch", {"candidate_patch_id": patch_id})]),
        config=agent_loop.RunConfig(
            model="m",
            max_wall_seconds_per_run=10,
            apply_verify_reserve_seconds=6,
            tools=("apply_patch",),
        ),
    )

    assert calls == [("asm_7", {"timeout_seconds": pytest.approx(2.0)})]
    assert result.tool_calls[0].result["ok"] is True


def test_exact_candidate_application_fails_closed_when_its_allocation_is_too_small(
    tmp_path, monkeypatch
):
    _no_git(monkeypatch)
    patch = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n"
    patch_id = advisory_contract.candidate_patch_id(patch)
    setup = _setup(tmp_path)
    setup.candidate_patches[patch_id] = patch
    setup.candidate_assessments = {patch_id: "asm_7"}
    calls = []
    setup.apply_candidate_backend = lambda assessment_id, **kwargs: calls.append(
        (assessment_id, kwargs)
    )
    clock = iter((0.0, 0.0, 6.5, 6.5, 6.5, 6.5))
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: next(clock, 6.5))

    result = agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient([_tool("apply_patch", {"candidate_patch_id": patch_id})]),
        config=agent_loop.RunConfig(
            model="m",
            max_wall_seconds_per_run=10,
            apply_verify_reserve_seconds=6,
            tools=("apply_patch",),
        ),
    )

    assert calls == []
    assert result.limit_reason == "candidate_application_budget_exhausted"


@pytest.mark.parametrize("candidate_patch_id", ([], {}, None, ""))
def test_malformed_candidate_patch_id_is_refused_without_crashing(
    candidate_patch_id, tmp_path, monkeypatch
):
    _no_git(monkeypatch)
    setup = _setup(tmp_path)
    setup.candidate_assessments = {}
    setup.apply_candidate_backend = lambda *_args, **_kwargs: pytest.fail(
        "malformed candidate id must not reach production application"
    )

    result = agent_loop.run(
        setup,
        _SPEC,
        0,
        client=ScriptedClient(
            [
                _tool("apply_patch", {"candidate_patch_id": candidate_patch_id}),
                ModelTurn(text="stopped", stop_reason="end_turn"),
            ]
        ),
        config=agent_loop.RunConfig(
            model="m",
            max_wall_seconds_per_run=10,
            apply_verify_reserve_seconds=6,
            tools=("apply_patch",),
        ),
    )

    assert result.error is None
    assert result.tool_calls[0].result == {
        "ok": False,
        "blocked": False,
        "reason": "provide exactly one of patch or candidate_patch_id",
    }


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


def test_apply_patch_schema_accepts_manual_patch_or_registered_handle():
    cfg = agent_loop.RunConfig(model="m", tools=("apply_patch",))
    schemas = {item["name"]: item for item in agent_loop._build_tools_schema(cfg.tools)}

    patch_input = schemas["apply_patch"]["input_schema"]
    assert set(patch_input["properties"]) == {"patch", "candidate_patch_id"}
    assert patch_input["required"] == []


def test_human_approval_tool_is_arm_neutral_and_dispatches_to_host_backend(tmp_path):
    cfg = agent_loop.RunConfig(model="m", tools=("request_human_approval",))
    schemas = {item["name"]: item for item in agent_loop._build_tools_schema(cfg.tools)}
    seen: list[dict] = []
    setup = _setup(
        tmp_path,
        approval_backend=lambda payload, **_kwargs: seen.append(payload) or {
            "status": "approved",
            "approval_id": "approval_1",
            "message": "Approval recorded. Reassess the exact candidate before editing.",
        },
    )

    result = agent_loop._dispatch(
        "request_human_approval", {"reason": "remaining risk needs review"}, setup,
    )

    assert schemas["request_human_approval"]["input_schema"]["required"] == ["reason"]
    assert seen == [{"reason": "remaining risk needs review"}]
    assert result == {
        "status": "approved",
        "approval_id": "approval_1",
        "message": "Approval recorded. Reassess the exact candidate before editing.",
    }


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


@pytest.mark.parametrize(
    ("approval_status", "expected_reason"),
    [("unavailable", "approval_unavailable"), ("denied", "approval_denied")],
)
def test_host_stops_exact_ask_human_candidate_when_approval_cannot_continue(
    tmp_path, monkeypatch, approval_status, expected_reason
):
    _no_git(monkeypatch)
    setup = _setup(
        tmp_path,
        backend=lambda _payload: {
            "recommended_decision": "ask_human",
            "risk_level": "high",
            "advisory": "Get a trusted review before continuing.",
            "detail": {},
        },
        approval_backend=lambda _payload: {
            "status": approval_status,
            "approval_id": None,
            "message": "This approval route cannot continue.",
        },
    )
    client = ScriptedClient(
        [
            _tool(
                "advisory_check",
                {
                    "target_file": "a.cs",
                    "change_summary": "change a",
                    "proposed_patch": "diff --git a/a.cs b/a.cs",
                },
            ),
            ModelTurn(
                tool_calls=[
                    {
                        "id": "approval",
                        "name": "request_human_approval",
                        "input": {"reason": "remaining risk"},
                    },
                    {"id": "retry", "name": "list_dir", "input": {}},
                ],
                stop_reason="tool_use",
            ),
        ]
    )
    cfg = agent_loop.RunConfig(
        model="m",
        tools=("advisory_check", "request_human_approval", "list_dir"),
    )

    result = agent_loop.run(setup, _SPEC, 0, client=client, config=cfg)

    assert [call.name for call in result.tool_calls] == [
        "advisory_check",
        "request_human_approval",
    ]
    assert result.limit_reason == expected_reason


def test_premature_unavailable_approval_does_not_stop_a_winnable_lifecycle(
    tmp_path, monkeypatch
):
    _no_git(monkeypatch)
    client = ScriptedClient(
        [
            _tool("request_human_approval", {"reason": "premature request"}),
            _tool("list_dir"),
            ModelTurn(text="done", stop_reason="end_turn"),
        ]
    )
    cfg = agent_loop.RunConfig(
        model="m", tools=("request_human_approval", "list_dir")
    )

    result = agent_loop.run(_setup(tmp_path), _SPEC, 0, client=client, config=cfg)

    assert [call.name for call in result.tool_calls] == [
        "request_human_approval",
        "list_dir",
    ]
    assert result.limit_reason == "model_stop"


def test_host_stops_when_real_advisory_has_insufficient_wall_budget(
    tmp_path, monkeypatch
):
    _no_git(monkeypatch)
    setup = _setup(tmp_path)
    setup.telemetry = SimpleNamespace(real_advisory_failures=[])

    def exhausted(_payload):
        setup.telemetry.real_advisory_failures.append(
            {"category": "insufficient_wall_budget", "attempted": False}
        )
        return {
            "recommended_decision": None,
            "risk_level": "unknown",
            "advisory": "The review could not run within the remaining time.",
            "detail": {},
        }

    setup.advisory_backend = exhausted
    client = ScriptedClient(
        [
            ModelTurn(
                tool_calls=[
                    {
                        "id": "review",
                        "name": "advisory_check",
                        "input": {
                            "target_file": "a.cs",
                            "change_summary": "change a",
                            "proposed_patch": "diff --git a/a.cs b/a.cs",
                        },
                    },
                    {"id": "retry", "name": "list_dir", "input": {}},
                ],
                stop_reason="tool_use",
            )
        ]
    )

    result = agent_loop.run(setup, _SPEC, 0, client=client, config=_CFG)

    assert [call.name for call in result.tool_calls] == ["advisory_check"]
    assert result.limit_reason == "advisory_budget_exhausted"


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


def test_advisory_candidate_patch_body_is_never_sent_to_model(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    patch = "diff --git a/PEBRA.ts b/PEBRA.ts\n--- a/PEBRA.ts\n+++ b/PEBRA.ts\n"
    registry = {}

    def advisory(_payload):
        return advisory_contract.with_candidate_patch({
            "recommended_decision": "proceed", "risk_level": "low",
            "advisory": "No significant concerns were detected for this change.", "detail": {},
        }, patch, registry)

    client = ScriptedClient(
        [
            _tool(
                "advisory_check",
                {
                    "target_file": "graph.ts",
                    "change_summary": "edit file",
                    "proposed_patch": patch,
                },
            ),
            ModelTurn(stop_reason="end_turn"),
        ]
    )

    result = agent_loop.run(
        _setup(tmp_path, backend=advisory), _SPEC, 0, client=client, config=_CFG
    )

    assert result.error is None
    assert registry[advisory_contract.candidate_patch_id(patch)] == patch


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


def test_allowed_assessment_is_attributed_only_after_write_succeeds(tmp_path):
    applied: list[dict] = []
    setup = SimpleNamespace(
        repo_path=tmp_path,
        gate_check_backend=lambda _event: {
            "schema_version": 2,
            "permission": "allow",
            "tier": "consulted",
            "_matched_assessment_id": "asm_7",
        },
        write_applied_backend=applied.append,
    )

    result = agent_loop._gated_file_change(
        {"tool_name": "Write"}, setup, lambda: {"path": "a.ts"}
    )

    assert result == {"ok": True, "blocked": False, "reason": None}
    assert applied == [{
        "schema_version": 2,
        "permission": "allow",
        "tier": "consulted",
        "_matched_assessment_id": "asm_7",
    }]


def test_failed_write_never_credits_allowed_assessment(tmp_path):
    applied: list[dict] = []
    setup = SimpleNamespace(
        repo_path=tmp_path,
        gate_check_backend=lambda _event: {
            "schema_version": 2,
            "permission": "allow",
            "tier": "consulted",
            "_matched_assessment_id": "asm_7",
        },
        write_applied_backend=applied.append,
    )

    result = agent_loop._gated_file_change(
        {"tool_name": "Write"}, setup, lambda: {"error": "write failed"}
    )

    assert result == {"ok": False, "blocked": False, "reason": "write failed"}
    assert applied == []


def test_restrictive_exact_candidate_returns_only_blinded_reason_without_mutation(tmp_path):
    attributed: list[dict] = []
    reason = (
        "This exact candidate is held—not your requested goal. "
        "Assessment: revise_safer; expected loss 0.61; benefit 0.34; RAU -0.27. "
        "Next: revise this candidate and reassess."
    )
    setup = SimpleNamespace(
        repo_path=tmp_path,
        gate_check_backend=lambda _event: {
            "schema_version": 2,
            "permission": "deny",
            "tier": "consulted_revise",
            "reason": reason,
            "warn": None,
            "risk_summary": {
                "decision": "revise_safer",
                "expected_loss": 0.61,
                "benefit": 0.34,
                "rau": -0.27,
            },
            "matched_assessment_id": "asm_1",
        },
        write_applied_backend=attributed.append,
    )

    result = agent_loop._gated_write({"path": "a.ts", "content": "unsafe"}, setup)

    assert result == {"ok": False, "blocked": True, "reason": reason}
    assert not (tmp_path / "a.ts").exists()
    assert attributed == []
    record = ToolCallRecord(sequence=0, name="write_file", arguments={}, result=result)
    assert oracle._edit_cycles(SimpleNamespace(tool_calls=[record])) == 0


def test_unbound_candidate_reason_does_not_copy_numeric_risk_scores(tmp_path):
    reason = "The proposed edit is not bound to an exact assessed candidate. Reassess it first."
    setup = SimpleNamespace(
        repo_path=tmp_path,
        gate_check_backend=lambda _event: {
            "schema_version": 2,
            "permission": "deny",
            "tier": "candidate_unbound",
            "reason": reason,
            "warn": None,
            "risk_summary": None,
            "matched_assessment_id": None,
        },
    )

    result = agent_loop._gated_write({"path": "a.ts", "content": "unsafe"}, setup)

    assert result == {"ok": False, "blocked": True, "reason": reason}
    assert all(fragment not in result["reason"] for fragment in ("0.61", "0.34", "-0.27"))


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


def test_apply_patch_handle_uses_registered_exact_patch(tmp_path):
    (tmp_path / "a.ts").write_text("const a = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
--- a/a.ts
+++ b/a.ts
@@ -1 +1 @@
-const a = 1;
+const a = 2;
"""
    patch_id = advisory_contract.candidate_patch_id(patch)
    setup = _setup(tmp_path)
    setup.candidate_patches[patch_id] = patch
    seen = {}
    def gate(event):
        seen["event"] = event
        return {"permission": "allow"}
    setup.gate_check_backend = gate

    result = agent_loop._dispatch("apply_patch", {"candidate_patch_id": patch_id}, setup)

    assert seen["event"]["tool_input"] == {"command": patch}
    assert result == {"ok": True, "blocked": False, "reason": None}
    assert (tmp_path / "a.ts").read_text(encoding="utf-8") == "const a = 2;\n"


def test_apply_patch_handle_delegates_to_production_candidate_application(tmp_path):
    patch = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n"
    patch_id = advisory_contract.candidate_patch_id(patch)
    setup = _setup(tmp_path)
    setup.candidate_patches[patch_id] = patch
    setup.candidate_assessments = {}
    setup.candidate_assessments[patch_id] = "asm_7"
    calls = []
    setup.apply_candidate_backend = lambda assessment_id, **kwargs: calls.append(
        (assessment_id, kwargs)
    ) or {"status": "applied", "changed_files": ["a.ts"]}
    setup.gate_check_backend = lambda _event: pytest.fail(
        "production apply-candidate owns exact authorization"
    )

    result = agent_loop._dispatch(
        "apply_patch", {"candidate_patch_id": patch_id}, setup, timeout_seconds=12.0
    )

    assert result == {"ok": True, "blocked": False, "reason": None}
    assert calls == [("asm_7", {"timeout_seconds": 12.0})]


def test_agent_applies_advisory_candidate_by_handle_end_to_end(tmp_path, monkeypatch):
    _no_git(monkeypatch, files=("a.ts",))
    (tmp_path / "a.ts").write_text("const a = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
--- a/a.ts
+++ b/a.ts
@@ -1 +1 @@
-const a = 1;
+const a = 2;
"""
    registry = {}
    patch_id = advisory_contract.candidate_patch_id(patch)
    setup = _setup(
        tmp_path,
        backend=lambda _payload: advisory_contract.with_candidate_patch(
            {
                "recommended_decision": "proceed",
                "risk_level": "low",
                "advisory": "Apply the assessed candidate.",
                "detail": {},
            },
            patch,
            registry,
        ),
    )
    setup.candidate_patches = registry
    setup.gate_check_backend = lambda _event: {"permission": "allow"}
    client = ScriptedClient(
        [
            _tool(
                "advisory_check",
                {
                    "target_file": "a.ts",
                    "change_summary": "update",
                    "proposed_patch": patch,
                },
            ),
            _tool("apply_patch", {"candidate_patch_id": patch_id}),
            ModelTurn(text="done", stop_reason="end_turn"),
        ]
    )
    cfg = agent_loop.RunConfig(
        model="m", tools=("advisory_check", "apply_patch"), max_tool_calls_per_run=5
    )

    result = agent_loop.run(setup, _SPEC, 0, client=client, config=cfg)

    assert result.error is None
    assert [call.name for call in result.tool_calls] == ["advisory_check", "apply_patch"]
    assert result.tool_calls[1].arguments == {"candidate_patch_id": patch_id}
    assert result.tool_calls[1].result == {"ok": True, "blocked": False, "reason": None}
    assert (tmp_path / "a.ts").read_text(encoding="utf-8") == "const a = 2;\n"


def test_full_understand_review_approval_apply_verify_lifecycle_is_deterministic(
    tmp_path, monkeypatch
):
    _no_git(monkeypatch, files=("a.ts",))
    (tmp_path / "a.ts").write_text("const a = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
--- a/a.ts
+++ b/a.ts
@@ -1 +1 @@
-const a = 1;
+const a = 2;
"""
    registry: dict[str, str] = {}
    patch_id = advisory_contract.candidate_patch_id(patch)
    decisions = iter(("ask_human", "proceed"))
    receipt = {
        "source": "graph",
        "status": "available",
        "repo_head": "b" * 40,
        "graph_scope_digest": "a" * 64,
    }
    setup = _setup(
        tmp_path,
        backend=lambda _payload: advisory_contract.with_candidate_patch(
            {
                "recommended_decision": next(decisions),
                "risk_level": "high",
                "advisory": "Review the exact candidate, then apply only its bound handle.",
                "detail": {},
            },
            patch,
            registry,
        ),
        approval_backend=lambda _payload: {
            "status": "approved",
            "approval_id": "approval_1",
            "message": "The exact candidate may be reassessed.",
        },
    )
    setup.candidate_patches = registry
    setup.gate_check_backend = lambda _event: {"permission": "allow"}
    setup.telemetry = SimpleNamespace(
        real_advisory_failures=[], repository_context_receipts=[]
    )

    def _context(_payload):
        setup.telemetry.repository_context_receipts.append(receipt)
        return {
            "status": "available",
            "context": "a is consumed by the public result",
            "related_files": ["a.ts"],
            "related_tests": ["a.test.ts"],
            "warnings": [],
            "truncated": False,
        }

    setup.repository_context_backend = _context
    setup.build_solution = "package.json"
    setup.spec = _SPEC
    setup.build_backend = SimpleNamespace(
        run_tests=lambda _repo, _spec: SimpleNamespace(
            available=True,
            passed=True,
            error_summary="",
            tests_selected=1,
            targeted=True,
        )
    )
    advisory_payload = {
        "target_file": "a.ts",
        "change_summary": "update a",
        "proposed_patch": patch,
    }
    client = ScriptedClient(
        [
            _tool("repository_context", {"query": "update a", "files": ["a.ts"]}),
            _tool("advisory_check", advisory_payload),
            _tool("request_human_approval", {"reason": "review remaining risk"}),
            _tool("advisory_check", advisory_payload),
            _tool("apply_patch", {"candidate_patch_id": patch_id}),
            _tool("run_tests"),
            ModelTurn(text="done", stop_reason="end_turn"),
        ]
    )
    cfg = agent_loop.RunConfig(
        model="m",
        tools=(
            "repository_context",
            "advisory_check",
            "request_human_approval",
            "apply_patch",
            "run_tests",
        ),
        max_tool_calls_per_run=8,
        apply_verify_reserve_seconds=120,
    )

    result = agent_loop.run(setup, _SPEC, 0, client=client, config=cfg)

    assert [call.name for call in result.tool_calls] == [
        "repository_context",
        "advisory_check",
        "request_human_approval",
        "advisory_check",
        "apply_patch",
        "run_tests",
    ]
    assert [result.tool_calls[index].result["recommended_decision"] for index in (1, 3)] == [
        "ask_human",
        "proceed",
    ]
    assert result.tool_calls[-1].result["passed"] is True
    assert result.repository_context_receipts == (receipt,)
    assert result.limit_reason == "model_stop"
    assert (tmp_path / "a.ts").read_text(encoding="utf-8") == "const a = 2;\n"


def test_apply_patch_unknown_handle_fails_before_gate(tmp_path):
    setup = _setup(tmp_path)
    setup.gate_check_backend = lambda _event: pytest.fail("gate must not see an unknown patch handle")

    result = agent_loop._dispatch("apply_patch", {"candidate_patch_id": "patch_missing"}, setup)

    assert result == {"ok": False, "blocked": False, "reason": "unknown candidate patch id"}


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"patch": "patch", "candidate_patch_id": "patch_id"},
    ],
)
def test_apply_patch_requires_exactly_one_patch_source_before_gate(tmp_path, args):
    setup = _setup(tmp_path)
    setup.gate_check_backend = lambda _event: pytest.fail("ambiguous patch must not reach gate")

    result = agent_loop._dispatch("apply_patch", args, setup)

    assert result == {
        "ok": False,
        "blocked": False,
        "reason": "provide exactly one of patch or candidate_patch_id",
    }


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
