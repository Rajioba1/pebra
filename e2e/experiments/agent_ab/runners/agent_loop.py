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
from e2e.experiments.agent_ab.runners.model_client import ScriptExhausted
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
_HARNESS_PATH_PREFIXES = (".codegraph/", ".pebra/")
_PEBRA_GITIGNORE_ENTRY = ".pebra/"

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
        return _gated_write(args, setup)
    if name == "list_dir":
        return tool_impl.list_dir(args.get("path"), repo)
    if name == "search_grep":
        return tool_impl.search_grep(args.get("pattern", ""), repo,
                                     path=args.get("path"), file_glob=args.get("file_glob"))
    if name == "run_build":
        return tool_impl.run_build(repo, sln=setup.build_solution)
    if name == "run_tests":
        return tool_impl.run_tests(repo, sln=setup.build_solution)
    if name == advisory_contract.TOOL_NAME:
        return tool_impl.advisory_check(args, setup.advisory_backend)
    return {"error": f"unknown tool {name!r}"}


def _gated_write(args: dict[str, Any], setup: "ArmSetup") -> dict[str, Any]:
    """Route a write through the arm's gate-check backend, then normalize to a blinded ``{ok, blocked}``
    shape — IDENTICAL in both arms; only the value differs (control's sham always allows). A gate
    ``deny``/``ask`` blocks the write (nothing is written) with an arm-neutral reason; a gate ERROR
    fails OPEN (the write proceeds) so a broken gate never bricks the run."""
    path = args.get("path", "")
    event = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": str(setup.repo_path)}
    try:
        decision = setup.gate_check_backend(event)
    except Exception:  # noqa: BLE001 - a gate failure must never block the experiment write (fail-open)
        decision = {"permission": "allow"}
    # FIXED schema in every case and BOTH arms: {"ok", "blocked", "reason"}. A per-outcome key set (e.g.
    # a treatment-only "reason") would let the agent infer its arm from the shape alone, so the key set is
    # invariant; only the values differ (control's sham never denies, so it only ever sees ok/allow).
    if isinstance(decision, dict) and decision.get("permission") in ("deny", "ask"):
        return {"ok": False, "blocked": True,
                "reason": decision.get("reason")
                or "A pre-edit check asked you to consult before making this change."}
    result = tool_impl.write_file(path, args.get("content", ""), setup.repo_path)
    if "error" in result:
        return {"ok": False, "blocked": False, "reason": result["error"]}
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
    turn_count = 0
    timed_out = False
    error: str | None = None
    final_stop_reason: str | None = None
    served_models: list[str] = []

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
            turn_count += 1
            final_stop_reason = turn.stop_reason
            if turn.served_model and turn.served_model not in served_models:
                served_models.append(turn.served_model)
            if turn.text:
                transcript.append(turn.text)
            messages.append({"role": "assistant", "content": _turn_to_content(turn)})
            if not turn.tool_calls:
                break

            results_content: list[dict[str, Any]] = []
            for tc in turn.tool_calls:
                if seq >= config.max_tool_calls_per_run:
                    results_content.append({"type": "tool_result", "tool_use_id": tc["id"],
                                            "content": json.dumps({"error": "tool-call limit reached"})})
                    continue
                name, args = tc["name"], tc.get("input", {})
                result = _dispatch(name, args, setup)
                # Blinding: scan harness-authored outputs (advisory result AND any write reason — a gate
                # deny or a write error), never file reads/content. Any reason text reaches the model.
                if name == advisory_contract.TOOL_NAME or (name == "write_file" and result.get("reason")):
                    blinding_presend_check([json.dumps(result)])
                records.append(ToolCallRecord(sequence=seq, name=name, arguments=args, result=result))
                seq += 1
                results_content.append({"type": "tool_result", "tool_use_id": tc["id"],
                                        "content": json.dumps(result)})
            messages.append({"role": "user", "content": results_content})
    except BlindingViolationError:
        raise  # not caught: a blinding violation aborts the run loudly
    except (NotImplementedError, ScriptExhausted, KeyError, TypeError):
        raise  # a genuine unimplemented path is a programmer error — surface it, don't mask as errored
    except Exception as exc:  # noqa: BLE001 - a live client/API error (auth/rate/network) is captured
        error = f"{type(exc).__name__}: {exc}"  # into the result so one run's failure doesn't crash the batch

    modified = _git_diff_name_only(setup.repo_path)
    return SubjectResult(
        task_id=spec.task_id, arm=setup.arm, seed=seed,
        transcript=tuple(transcript), tool_calls=tuple(records), modified_files=modified,
        duration_seconds=round(time.monotonic() - start, 2), timed_out=timed_out, error=error,
        final_stop_reason=final_stop_reason, turn_count=turn_count,
        served_models=tuple(served_models),
    )
