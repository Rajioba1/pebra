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
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab.metrics import blinding
from e2e.experiments.agent_ab.models import SubjectResult, ToolCallRecord
from e2e.experiments.agent_ab.runners import tool_impl
from e2e.experiments.agent_ab.tools import advisory_contract

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
    tools: tuple[str, ...] = ("read_file", "write_file", "list_dir", "search_grep",
                              "run_build", "run_tests", "advisory_check")


_SEED_USER = "Please complete the task now, using the tools available."

# Inline tool schemas (deterministic; advisory_check comes from the shared contract).
_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {"description": "Read a repo file by relative path.",
                  "input_schema": {"type": "object",
                                   "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    "write_file": {"description": "Write/overwrite a repo file by relative path.",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"},
                                                   "content": {"type": "string"}},
                                    "required": ["path", "content"]}},
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
    for name in names:
        if name == advisory_contract.TOOL_NAME:
            schema.append({"name": name, "description": advisory_contract.TOOL_DESCRIPTION,
                           "input_schema": advisory_contract.INPUT_SCHEMA})
        elif name in _TOOL_SCHEMAS:
            schema.append({"name": name, **_TOOL_SCHEMAS[name]})
    return schema


def blinding_presend_check(texts: list[str]) -> None:
    """Fail-closed. Scan harness-authored strings before they reach the model."""
    for text in texts:
        leaked, terms = blinding.scan_text(text or "")
        if leaked:
            raise BlindingViolationError(
                f"pre-send blinding check matched {terms!r}; aborting run (fail-closed)"
            )


def _dispatch(name: str, args: dict[str, Any], setup: "ArmSetup") -> dict[str, Any]:
    repo = setup.repo_path
    if name == "read_file":
        return tool_impl.read_file(args.get("path", ""), repo)
    if name == "write_file":
        return tool_impl.write_file(args.get("path", ""), args.get("content", ""), repo)
    if name == "list_dir":
        return tool_impl.list_dir(args.get("path"), repo)
    if name == "search_grep":
        return tool_impl.search_grep(args.get("pattern", ""), repo,
                                     path=args.get("path"), file_glob=args.get("file_glob"))
    if name == "run_build":
        return tool_impl.run_build(repo)
    if name == "run_tests":
        return tool_impl.run_tests(repo)
    if name == advisory_contract.TOOL_NAME:
        return tool_impl.advisory_check(args, setup.advisory_backend)
    return {"error": f"unknown tool {name!r}"}


def _git_diff_name_only(repo_path: Path) -> tuple[str, ...]:
    proc = subprocess.run(["git", "diff", "HEAD", "--name-only"], cwd=str(repo_path),
                          capture_output=True, text=True)
    return tuple(ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines() if ln.strip())


def _turn_to_content(turn) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for tc in turn.tool_calls:
        content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                        "input": tc.get("input", {})})
    return content


def run(setup: "ArmSetup", spec, seed: int, *, client, config: RunConfig) -> SubjectResult:
    """Drive one blinded subject run. Returns a SubjectResult with build/test fields UNSET (the
    orchestrator fills them after injecting the hidden evaluator tests)."""
    blinding_presend_check([setup.subject_prompt, _SEED_USER])  # only harness-authored strings
    start = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": _SEED_USER}]
    tools = _build_tools_schema(config.tools)
    transcript: list[str] = [setup.subject_prompt, _SEED_USER]
    records: list[ToolCallRecord] = []
    seq = 0
    timed_out = False
    error: str | None = None

    try:
        while True:
            # Wall-clock is checked BETWEEN turns (not during a tool call). A single tool is still
            # bounded: dotnet_harness.run_build/run_tests pass their own subprocess timeout=, so a hung
            # build/test cannot run unbounded past this guard.
            if time.monotonic() - start >= config.max_wall_seconds_per_run:
                timed_out = True
                break
            if seq >= config.max_tool_calls_per_run:
                break
            turn = client.send(messages, tools, setup.subject_prompt,
                               max_tokens=config.max_output_tokens_per_turn)
            if turn.text:
                transcript.append(turn.text)
            messages.append({"role": "assistant", "content": _turn_to_content(turn)})
            if turn.stop_reason in ("end_turn", "max_tokens") or not turn.tool_calls:
                break

            results_content: list[dict[str, Any]] = []
            for tc in turn.tool_calls:
                if seq >= config.max_tool_calls_per_run:
                    results_content.append({"type": "tool_result", "tool_use_id": tc["id"],
                                            "content": json.dumps({"error": "tool-call limit reached"})})
                    continue
                name, args = tc["name"], tc.get("input", {})
                result = _dispatch(name, args, setup)
                # Blinding: scan ONLY the advisory tool's output (harness-controlled), never file reads.
                if name == advisory_contract.TOOL_NAME:
                    blinding_presend_check([json.dumps(result)])
                records.append(ToolCallRecord(sequence=seq, name=name, arguments=args, result=result))
                seq += 1
                results_content.append({"type": "tool_result", "tool_use_id": tc["id"],
                                        "content": json.dumps(result)})
            messages.append({"role": "user", "content": results_content})
    except BlindingViolationError:
        raise  # not caught: a blinding violation aborts the run loudly
    except NotImplementedError:
        raise  # a genuine unimplemented path is a programmer error — surface it, don't mask as errored
    except Exception as exc:  # noqa: BLE001 - a live client/API error (auth/rate/network) is captured
        error = f"{type(exc).__name__}: {exc}"  # into the result so one run's failure doesn't crash the batch

    modified = _git_diff_name_only(setup.repo_path)
    return SubjectResult(
        task_id=spec.task_id, arm=setup.arm, seed=seed,
        transcript=tuple(transcript), tool_calls=tuple(records), modified_files=modified,
        duration_seconds=round(time.monotonic() - start, 2), timed_out=timed_out, error=error,
    )
