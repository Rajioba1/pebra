"""The subject agent turn loop — real model reasoning, logged/confined tools, deterministic plumbing.

Everything here is deterministic and unit-tested via ``ScriptedClient`` EXCEPT the single
``client.send`` call. The loop dispatches tool calls through ``tool_impl`` (confined to the clone),
enforces tool-call and wall-time limits, and captures the transcript / ordered ToolCallRecords / diff
into a ``SubjectResult``. It does NOT run the evaluator build/test — that is the orchestrator's
post-agent step (it injects the hidden evaluator tests first). ``SubjectResult.build_*``/``test_*`` are
left unset here and filled by the orchestrator.

Blinding pre-send check (fail-closed) scans ONLY harness-authored strings we control — the subject
prompt and the advisory tool's OUTPUT — never the agent's file reads/listings/searches (repo content
like "graph"/"oracle" legitimately appears in this UI codebase and must not abort a run). No pebra import.
"""

from __future__ import annotations

import json
import inspect
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab.metrics import blinding
from e2e.experiments.agent_ab.models import MUTATING_TOOLS, SubjectResult, ToolCallRecord
from e2e.experiments.agent_ab.runners import run_artifacts, subject_protocol, token_accounting, tool_impl
from e2e.experiments.agent_ab.runners.model_client import ModelTurn, ScriptExhausted
from e2e.experiments.agent_ab.tools import (
    advisory_contract,
    approval_contract,
    repository_context_contract,
)
from e2e.utils import cli_harness

if False:  # typing only; avoid importing run_pair at runtime (it imports adapters)
    from e2e.experiments.agent_ab.runners.run_pair import ArmSetup


class BlindingViolationError(RuntimeError):
    """A harness-authored string that would unblind the subject was about to be sent."""


@dataclass(frozen=True)
class RunConfig:
    model: str
    max_tool_calls_per_run: int = 50
    max_wall_seconds_per_run: int = 600
    max_output_tokens_per_turn: int = 4096
    apply_verify_reserve_seconds: float = 0.0
    tools: tuple[str, ...] = ("read_file", "edit_file", "apply_patch", "write_file", "list_dir", "search_grep",
                              "run_build", "run_tests", "repository_context", "advisory_check",
                              "request_human_approval")

    def __post_init__(self) -> None:
        reserve = self.apply_verify_reserve_seconds
        if (
            isinstance(reserve, bool)
            or not isinstance(reserve, (int, float))
            or not math.isfinite(reserve)
            or reserve < 0
            or reserve >= self.max_wall_seconds_per_run
        ):
            raise ValueError(
                "apply_verify_reserve_seconds must be a non-negative number smaller than "
                "max_wall_seconds_per_run"
            )


@dataclass
class _LifecycleState:
    """Host-observed governance state; never exposed to the subject model."""

    current_decision: str | None = None

    def observe(self, name: str, result: dict[str, Any], setup: "ArmSetup") -> str | None:
        if name == advisory_contract.TOOL_NAME:
            decision = result.get("recommended_decision")
            self.current_decision = decision if isinstance(decision, str) else None
            failures = getattr(
                getattr(setup, "telemetry", None), "real_advisory_failures", ()
            )
            if failures:
                latest = failures[-1]
                if (
                    isinstance(latest, dict)
                    and latest.get("category") == "insufficient_wall_budget"
                    and latest.get("attempted") is False
                ):
                    return "advisory_budget_exhausted"
        elif name == approval_contract.TOOL_NAME and self.current_decision == "ask_human":
            status = result.get("status")
            if status == "denied":
                return "approval_denied"
            if status == "unavailable":
                return "approval_unavailable"
        return None


_SEED_USER = "Please complete the task now, using the tools available."
_HARNESS_PATH_PREFIXES = (".codegraph/", ".pebra/", ".agent-instructions/")
_PEBRA_GITIGNORE_ENTRY = ".pebra/"
_MIN_BOUNDED_TOOL_SECONDS = 1.0

# Inline tool schemas (deterministic; advisory_check comes from the shared contract).
_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {"description": "Read a repo file by relative path.",
                  "input_schema": {"type": "object",
                                   "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    "write_file": {"description": "Create a file or completely replace it. Prefer edit_file for existing files.",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"},
                                                   "content": {"type": "string"}},
                                    "required": ["path", "content"]}},
    "edit_file": {"description": "Replace a unique string in an existing repo file.",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"},
                                                   "old_string": {"type": "string"},
                                                   "new_string": {"type": "string"},
                                                   "replace_all": {"type": "boolean"}},
                                    "required": ["path", "old_string", "new_string"]}},
    "apply_patch": {"description": (
                        "Atomically apply a git-style unified patch to one or more files. Provide "
                        "exactly one of patch or a candidate_patch_id returned by advisory_check."
                    ),
                    "input_schema": {"type": "object",
                                     "properties": {
                                         "patch": {"type": "string"},
                                         "candidate_patch_id": {"type": "string"},
                                     },
                                     "required": []}},
    "list_dir": {"description": "List entries of a repo directory.",
                 "input_schema": {"type": "object",
                                  "properties": {"path": {"type": "string"}}, "required": []}},
    "search_grep": {"description": "Search repo files for a substring.",
                    "input_schema": {"type": "object",
                                     "properties": {"pattern": {"type": "string"},
                                                    "path": {"type": "string"},
                                                    "file_glob": {"type": "string"}},
                                     "required": ["pattern"]}},
    "run_build": {"description": "Build the project and return pass/fail.",
                  "input_schema": {"type": "object", "properties": {}, "required": []}},
    "run_tests": {"description": "Run the project tests and return pass/fail.",
                  "input_schema": {"type": "object", "properties": {}, "required": []}},
}


def _build_tools_schema(names: tuple[str, ...]) -> list[dict[str, Any]]:
    schema: list[dict[str, Any]] = []
    expanded: list[str] = []
    for name in names:
        expanded.append(name)
        if name == "write_file" and "edit_file" not in names:
            expanded.append("edit_file")
    for name in expanded:
        if name == advisory_contract.TOOL_NAME:
            schema.append({"name": name, "description": advisory_contract.TOOL_DESCRIPTION,
                           "input_schema": advisory_contract.INPUT_SCHEMA})
        elif name == approval_contract.TOOL_NAME:
            schema.append({"name": name, "description": approval_contract.TOOL_DESCRIPTION,
                           "input_schema": approval_contract.INPUT_SCHEMA})
        elif name == repository_context_contract.TOOL_NAME:
            schema.append({
                "name": name,
                "description": repository_context_contract.TOOL_DESCRIPTION,
                "input_schema": repository_context_contract.INPUT_SCHEMA,
            })
        elif name in _TOOL_SCHEMAS:
            schema.append({"name": name, **_TOOL_SCHEMAS[name]})
    return schema


def blinding_presend_check(texts: list[str]) -> None:
    """Fail-closed. Scan harness-authored strings before they reach the model."""
    for text in texts:
        leaked, terms = blinding.scan_text(text or "")
        if leaked:
            redacted = tool_impl.model_safe_text(text or "")[:240]
            raise BlindingViolationError(
                f"pre-send blinding check matched {terms!r}; redacted_text={redacted!r}; "
                "aborting run (fail-closed)"
            )


def _dispatch(
    name: str,
    args: dict[str, Any],
    setup: "ArmSetup",
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    repo = setup.repo_path
    if name == "read_file":
        return tool_impl.read_file(args.get("path", ""), repo)
    if name == "write_file":
        return _gated_write(args, setup, timeout_seconds=timeout_seconds)
    if name == "edit_file":
        return _gated_edit(args, setup, timeout_seconds=timeout_seconds)
    if name == "apply_patch":
        return _gated_patch(args, setup, timeout_seconds=timeout_seconds)
    if name == "list_dir":
        return tool_impl.list_dir(args.get("path"), repo)
    if name == "search_grep":
        return tool_impl.search_grep(args.get("pattern", ""), repo,
                                     path=args.get("path"), file_glob=args.get("file_glob"))
    if name == "run_build":
        return tool_impl.run_build(repo, backend=getattr(setup, "build_backend", None),
                                   spec=getattr(setup, "spec", None), sln=setup.build_solution,
                                   timeout_seconds=timeout_seconds)
    if name == "run_tests":
        return tool_impl.run_tests(repo, backend=getattr(setup, "build_backend", None),
                                   spec=getattr(setup, "spec", None), sln=setup.build_solution,
                                   timeout_seconds=timeout_seconds)
    if name == repository_context_contract.TOOL_NAME:
        return tool_impl.repository_context(
            args,
            setup.repository_context_backend,
            timeout_seconds=timeout_seconds,
        )
    if name == advisory_contract.TOOL_NAME:
        return tool_impl.advisory_check(
            args,
            setup.advisory_backend,
            timeout_seconds=timeout_seconds,
        )
    if name == approval_contract.TOOL_NAME:
        return tool_impl.request_human_approval(
            args,
            setup.approval_backend,
            timeout_seconds=timeout_seconds,
        )
    return {"error": f"unknown tool {name!r}"}


def _is_exact_candidate_application(
    name: str, args: dict[str, Any], setup: "ArmSetup"
) -> bool:
    """Identify the one mutating tool allowed to enter the reserved closeout window."""
    if name != "apply_patch":
        return False
    patch_id = args.get("candidate_patch_id")
    if not isinstance(patch_id, str) or not patch_id:
        return False
    assessment_id = getattr(setup, "candidate_assessments", {}).get(patch_id)
    return isinstance(assessment_id, str) and callable(
        getattr(setup, "apply_candidate_backend", None)
    )


def _gated_write(
    args: dict[str, Any], setup: "ArmSetup", *, timeout_seconds: float | None = None
) -> dict[str, Any]:
    """Route a write through the arm's gate-check backend, then normalize to a blinded ``{ok, blocked}``
    shape — IDENTICAL in both arms; only the value differs (control's sham always allows). A gate
    ``deny``/``ask`` blocks the write (nothing is written) with an arm-neutral reason; a gate ERROR
    fails OPEN (the write proceeds) so a broken gate never bricks the run."""
    path = args.get("path", "")
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": path, "content": args.get("content", "")},
        "cwd": str(setup.repo_path),
    }
    return _gated_file_change(
        event,
        setup,
        lambda: tool_impl.write_file(path, args.get("content", ""), setup.repo_path),
        timeout_seconds=timeout_seconds,
    )


def _gated_edit(
    args: dict[str, Any], setup: "ArmSetup", *, timeout_seconds: float | None = None
) -> dict[str, Any]:
    path = args.get("path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = args.get("replace_all") is True
    event = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": path,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        },
        "cwd": str(setup.repo_path),
    }
    return _gated_file_change(
        event,
        setup,
        lambda: tool_impl.edit_file(
            path, old_string, new_string, setup.repo_path, replace_all=replace_all
        ),
        timeout_seconds=timeout_seconds,
    )


def _gated_patch(
    args: dict[str, Any], setup: "ArmSetup", *, timeout_seconds: float | None = None
) -> dict[str, Any]:
    patch_arg = args.get("patch")
    patch_id_arg = args.get("candidate_patch_id")
    has_patch = isinstance(patch_arg, str) and bool(patch_arg)
    has_patch_id = isinstance(patch_id_arg, str) and bool(patch_id_arg)
    if has_patch == has_patch_id:
        return {
            "ok": False,
            "blocked": False,
            "reason": "provide exactly one of patch or candidate_patch_id",
        }
    if has_patch_id:
        patch = setup.candidate_patches.get(patch_id_arg)
        if patch is None:
            return {"ok": False, "blocked": False, "reason": "unknown candidate patch id"}
        assessment_id = getattr(setup, "candidate_assessments", {}).get(patch_id_arg)
        production_apply = getattr(setup, "apply_candidate_backend", None)
        if isinstance(assessment_id, str) and callable(production_apply):
            try:
                kwargs = {}
                if timeout_seconds is not None:
                    kwargs["timeout_seconds"] = timeout_seconds
                production_apply(assessment_id, **kwargs)
            except Exception:  # noqa: BLE001 - production refusal is a blocked write, never fail-open
                return {
                    "ok": False,
                    "blocked": True,
                    "reason": "The exact assessed candidate was not authorized for application.",
                }
            write_applied = getattr(setup, "write_applied_backend", None)
            if callable(write_applied):
                write_applied({"_matched_assessment_id": assessment_id})
            return {"ok": True, "blocked": False, "reason": None}
    else:
        patch = patch_arg
    event = {
        "tool_name": "apply_patch",
        "tool_input": {"command": patch},
        "cwd": str(setup.repo_path),
    }
    return _gated_file_change(
        event,
        setup,
        lambda: tool_impl.apply_patch(patch, setup.repo_path),
        timeout_seconds=timeout_seconds,
    )


def _gated_file_change(
    event: dict[str, Any], setup: "ArmSetup", apply_change: Any,
    *, timeout_seconds: float | None = None,
) -> dict[str, Any]:
    try:
        parameters = inspect.signature(setup.gate_check_backend).parameters.values()
        accepts_timeout = any(
            parameter.name == "timeout_seconds"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        decision = (
            setup.gate_check_backend(event, timeout_seconds=timeout_seconds)
            if accepts_timeout
            else setup.gate_check_backend(event)
        )
    except cli_harness.GateContractError:
        raise
    except Exception:  # noqa: BLE001 - ordinary gate infrastructure failure stays fail-open
        decision = {"permission": "allow"}
    # FIXED schema in every case and BOTH arms: {"ok", "blocked", "reason"}. A per-outcome key set (e.g.
    # a treatment-only "reason") would let the agent infer its arm from the shape alone, so the key set is
    # invariant; only the values differ (control's sham never denies, so it only ever sees ok/allow).
    if isinstance(decision, dict) and decision.get("permission") in ("deny", "ask"):
        return {"ok": False, "blocked": True,
                "reason": decision.get("reason")
                or "A pre-edit check asked you to consult before making this change."}
    result = apply_change()
    if "error" in result:
        return {"ok": False, "blocked": False, "reason": result["error"]}
    write_applied = getattr(setup, "write_applied_backend", None)
    if callable(write_applied) and isinstance(decision, dict):
        write_applied(decision)
    return {"ok": True, "blocked": False, "reason": None}


def _git_diff_name_only(repo_path: Path) -> tuple[str, ...]:
    tracked = subprocess.run(["git", "diff", "HEAD", "--name-only"], cwd=str(repo_path),
                             capture_output=True, text=True)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=str(repo_path),
        capture_output=True, text=True,
    )
    names = set()
    for proc in (tracked, untracked):
        for ln in proc.stdout.splitlines():
            name = ln.strip().replace("\\", "/")
            if name and not _is_harness_diff_path(repo_path, name):
                names.add(name)
    return tuple(sorted(names))


def _is_harness_diff_path(repo_path: Path, name: str) -> bool:
    if any(name.startswith(prefix) for prefix in _HARNESS_PATH_PREFIXES):
        return True
    return name == ".gitignore" and _gitignore_diff_is_pebra_only(repo_path)


def _gitignore_diff_is_pebra_only(repo_path: Path) -> bool:
    proc = subprocess.run(["git", "diff", "HEAD", "--unified=0", "--", ".gitignore"],
                          cwd=str(repo_path), capture_output=True, text=True)
    changed: list[str] = []
    for line in proc.stdout.splitlines():
        if not line or line.startswith(("diff ", "index ", "--- ", "+++ ", "@@")):
            continue
        if line[0] in "+-":
            changed.append(line[1:].strip())
    return _PEBRA_GITIGNORE_ENTRY in changed and all(
        entry in {"", _PEBRA_GITIGNORE_ENTRY} for entry in changed
    )


def _turn_to_content(turn) -> list[dict[str, Any]]:
    if turn.provider_content:
        return [dict(block) for block in turn.provider_content]
    content: list[dict[str, Any]] = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for tc in turn.tool_calls:
        content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                        "input": tc.get("input", {})})
    return content


def run(
    setup: "ArmSetup",
    spec,
    seed: int,
    *,
    client,
    config: RunConfig,
    trace_path: Path | None = None,
    deadline_monotonic: float | None = None,
) -> SubjectResult:
    """Drive one blinded subject run. Returns a SubjectResult with build/test fields UNSET (the
    orchestrator fills them after injecting the hidden evaluator tests)."""
    blinding_presend_check([setup.subject_prompt, _SEED_USER])  # only harness-authored strings
    start = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": _SEED_USER}]
    tools = _build_tools_schema(config.tools)
    transcript: list[str] = [setup.subject_prompt, _SEED_USER]
    records: list[ToolCallRecord] = []
    seq = 0
    turn_count = 0
    timed_out = False
    error: str | None = None
    final_stop_reason: str | None = None
    served_models: list[str] = []
    turns: list[dict[str, Any]] = []
    tools_seen: list[dict[str, Any]] = []
    limit_reason: str | None = None
    configured_deadline = start + config.max_wall_seconds_per_run
    deadline = (
        min(configured_deadline, deadline_monotonic)
        if deadline_monotonic is not None
        else configured_deadline
    )
    lifecycle = _LifecycleState()
    model_turns: list[ModelTurn] = []
    understand_turns: list[ModelTurn] = []
    consumes_understand_result = False

    try:
        while True:
            # The provider and advisory share one deadline with application + host verification.
            # Once only the closeout reserve remains, do not spend it on another stochastic turn.
            turn_started = time.monotonic()
            remaining_before_turn = deadline - turn_started
            if remaining_before_turn <= 0:
                timed_out = True
                limit_reason = "wall_clock"
                break
            model_timeout = remaining_before_turn - config.apply_verify_reserve_seconds
            if model_timeout <= 0:
                limit_reason = "closeout_budget_reserved"
                break
            if seq >= config.max_tool_calls_per_run:
                limit_reason = "tool_call_limit"
                break
            turn = client.send(messages, tools, setup.subject_prompt,
                               max_tokens=config.max_output_tokens_per_turn,
                               timeout_seconds=model_timeout)
            turn_ended = time.monotonic()
            turn_count += 1
            model_turns.append(turn)
            requests_understand = any(
                call.get("name") == repository_context_contract.TOOL_NAME
                for call in turn.tool_calls
            )
            if requests_understand or consumes_understand_result:
                understand_turns.append(turn)
            consumes_understand_result = requests_understand
            final_stop_reason = turn.stop_reason
            if turn.served_model and turn.served_model not in served_models:
                served_models.append(turn.served_model)
            turns.append({
                "turn_index": turn_count - 1,
                "started_seconds": round(turn_started - start, 6),
                "ended_seconds": round(turn_ended - start, 6),
                "latency_seconds": round(turn_ended - turn_started, 6),
                "stop_reason": turn.stop_reason,
                "served_model": turn.served_model,
                "usage": {
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                    "cache_read_tokens": turn.cache_read_tokens,
                    "cache_write_tokens": turn.cache_write_tokens,
                },
                "text": turn.text,
                "tool_calls": [
                    {"id": tc.get("id"), "name": tc.get("name"), "input": tc.get("input", {})}
                    for tc in turn.tool_calls
                ],
            })
            if turn_ended - start >= config.max_wall_seconds_per_run:
                timed_out = True
                limit_reason = "wall_clock"
                break
            if turn.text:
                transcript.append(turn.text)
            messages.append({"role": "assistant", "content": _turn_to_content(turn)})
            if not turn.tool_calls:
                limit_reason = "model_stop"
                break

            results_content: list[dict[str, Any]] = []
            governance_stop = False
            for tc in turn.tool_calls:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    limit_reason = "wall_clock"
                    break
                if seq >= config.max_tool_calls_per_run:
                    limit_reason = "tool_call_limit"
                    results_content.append({"type": "tool_result", "tool_use_id": tc["id"],
                                            "content": json.dumps({"error": "tool-call limit reached"})})
                    continue
                name, args = tc["name"], tc.get("input", {})
                tool_started = time.monotonic()
                exact_candidate_application = _is_exact_candidate_application(
                    name, args, setup
                )
                if exact_candidate_application:
                    # Split the closeout reserve evenly. Exact production application may use the
                    # first half but cannot consume the second half reserved for host verification.
                    application_budget = config.apply_verify_reserve_seconds / 2
                    tool_timeout = (
                        min(
                            application_budget,
                            deadline - application_budget - tool_started,
                        )
                        if application_budget > 0
                        else deadline - tool_started
                    )
                    if tool_timeout < _MIN_BOUNDED_TOOL_SECONDS:
                        limit_reason = "candidate_application_budget_exhausted"
                        governance_stop = True
                        break
                else:
                    # Every model-directed tool other than exact production application is
                    # pre-closeout. Do not start one if its bounded budget would enter the reserve.
                    tool_timeout = (
                        deadline
                        - config.apply_verify_reserve_seconds
                        - tool_started
                    )
                    if tool_timeout < _MIN_BOUNDED_TOOL_SECONDS:
                        limit_reason = (
                            "advisory_budget_exhausted"
                            if name == advisory_contract.TOOL_NAME
                            else "closeout_budget_reserved"
                        )
                        governance_stop = True
                        break
                result = _dispatch(
                    name,
                    args,
                    setup,
                    timeout_seconds=tool_timeout,
                )
                tool_ended = time.monotonic()
                # Blinding: scan harness-authored outputs (advisory result AND any write reason — a gate
                # deny or a write error), never file reads/content. Any reason text reaches the model.
                if name == advisory_contract.TOOL_NAME:
                    blinding_presend_check([json.dumps(result)])
                elif name == approval_contract.TOOL_NAME or (
                    name in MUTATING_TOOLS and result.get("reason")
                ):
                    blinding_presend_check([json.dumps(result)])
                records.append(ToolCallRecord(sequence=seq, name=name, arguments=args, result=result))
                tools_seen.append({
                    "sequence": seq,
                    "turn_index": turn_count - 1,
                    "name": name,
                    "arguments": args,
                    "result": result,
                    "started_seconds": round(tool_started - start, 6),
                    "ended_seconds": round(tool_ended - start, 6),
                    "latency_seconds": round(tool_ended - tool_started, 6),
                    "blocked": result.get("blocked") if isinstance(result, dict) else None,
                    "advisory_decision": (
                        result.get("recommended_decision") if name == advisory_contract.TOOL_NAME
                        and isinstance(result, dict) else None
                    ),
                    "approval_status": (
                        result.get("status") if name == approval_contract.TOOL_NAME
                        and isinstance(result, dict) else None
                    ),
                })
                seq += 1
                results_content.append({"type": "tool_result", "tool_use_id": tc["id"],
                                        "content": json.dumps(result)})
                terminal_reason = lifecycle.observe(name, result, setup)
                if terminal_reason is not None:
                    limit_reason = terminal_reason
                    governance_stop = True
                    break
            if timed_out or governance_stop:
                break
            messages.append({"role": "user", "content": results_content})
    except BlindingViolationError:
        raise  # not caught: a blinding violation aborts the run loudly
    except (NotImplementedError, ScriptExhausted, KeyError, TypeError):
        raise  # a genuine unimplemented path is a programmer error — surface it, don't mask as errored
    except Exception as exc:  # noqa: BLE001 - a live client/API error (auth/rate/network) is captured
        error = f"{type(exc).__name__}: {exc}"  # into the result so one run's failure doesn't crash the batch
        limit_reason = "error"

    modified = _git_diff_name_only(setup.repo_path)
    protocol_read = _protocol_file_read(records)
    token_usage = token_accounting.summarize(model_turns)
    understand_turn_usage = token_accounting.summarize(
        understand_turns, label="understand_turn_usage"
    )
    result = SubjectResult(
        task_id=spec.task_id, arm=setup.arm, seed=seed,
        transcript=tuple(transcript), tool_calls=tuple(records), modified_files=modified,
        duration_seconds=round(time.monotonic() - start, 2), timed_out=timed_out, error=error,
        final_stop_reason=final_stop_reason, limit_reason=limit_reason, turn_count=turn_count,
        served_models=tuple(served_models),
        protocol_file_read=protocol_read,
        real_advisory_failures=tuple(
            getattr(getattr(setup, "telemetry", None), "real_advisory_failures", ())
        ),
        repository_context_receipts=tuple(
            getattr(
                getattr(setup, "telemetry", None), "repository_context_receipts", ()
            )
        ),
        token_usage=token_usage,
        understand_turn_usage=understand_turn_usage,
    )
    if trace_path is not None:
        _write_subject_trace(trace_path, result, config, turns, tools_seen, limit_reason)
    return result


def _write_subject_trace(
    path: Path,
    result: SubjectResult,
    config: RunConfig,
    turns: list[dict[str, Any]],
    tools_seen: list[dict[str, Any]],
    limit_reason: str | None,
) -> None:
    """Persist raw agent evidence beside the clone. This is debug-only; scoring reads RunOutcome."""
    payload = {
        "schema_version": "agent_ab.subject_trace.v1",
        "task_id": result.task_id,
        "arm": result.arm,
        "seed": result.seed,
        "model": config.model,
        "limits": {
            "max_tool_calls_per_run": config.max_tool_calls_per_run,
            "max_wall_seconds_per_run": config.max_wall_seconds_per_run,
            "max_output_tokens_per_turn": config.max_output_tokens_per_turn,
            "apply_verify_reserve_seconds": config.apply_verify_reserve_seconds,
        },
        "final": {
            "timed_out": result.timed_out,
            "limit_reason": limit_reason,
            "error": result.error,
            "final_stop_reason": result.final_stop_reason,
            "turn_count": result.turn_count,
            "duration_seconds": result.duration_seconds,
            "served_models": list(result.served_models),
            "protocol_file_read": result.protocol_file_read,
            "modified_files": list(result.modified_files),
            "reason": result.error or result.limit_reason,
            "real_advisory_failures": list(result.real_advisory_failures),
            "repository_context_receipts": list(result.repository_context_receipts),
        },
        "transcript": list(result.transcript),
        "token_usage": result.token_usage,
        "understand_turn_usage": result.understand_turn_usage,
        "turns": turns,
        "tool_calls": tools_seen,
    }
    run_artifacts.atomic_write_json(Path(path), payload)


def _protocol_file_read(records: list[ToolCallRecord]) -> bool:
    expected = subject_protocol.INSTRUCTION_REL_PATH.replace("\\", "/")
    return any(
        call.name == "read_file"
        and _strip_current_dir(str(call.arguments.get("path", "")).replace("\\", "/")) == expected
        for call in records
    )


def _strip_current_dir(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    return path
