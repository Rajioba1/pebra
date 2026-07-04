"""Load + validate the corpus: join tasks.jsonl (agent-facing) to oracles.jsonl (hidden labels).

Validation is the corpus's honesty guard:
  - the agent-facing text must contain none of the experiment/framing words (would leak the setup);
  - every task must have a matching oracle, a valid harm_label, and a non-empty expected_edit_scope;
  - risky tasks must declare a real harm mechanism; safe tasks must not be expected to break the build.
"""

from __future__ import annotations

import json
from pathlib import Path

from e2e.experiments.agent_ab.forbidden import CORPUS_FORBIDDEN_TERMS, match_terms
from e2e.experiments.agent_ab.models import TaskSpec

_CORPUS_DIR = Path(__file__).resolve().parent
_TASKS = _CORPUS_DIR / "tasks.jsonl"
_ORACLES = _CORPUS_DIR / "oracles.jsonl"

_VALID_HARM = {"risky", "safe"}
_REAL_HARM_TYPES = {"build_failure", "test_failure", "scope_drift"}


class CorpusError(ValueError):
    pass


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{path.name}:{i} is not valid JSON: {exc}") from exc
    return rows


def _agent_facing_text(task: dict) -> str:
    return " ".join([task.get("task_id", ""), task.get("description", ""),
                     *task.get("target_hints", [])])


def _leak_terms(text: str) -> list[str]:
    return list(match_terms(text, CORPUS_FORBIDDEN_TERMS))


def load_corpus(tasks_path: Path | None = None, oracles_path: Path | None = None) -> list[TaskSpec]:
    tasks = _read_jsonl(tasks_path or _TASKS)
    oracle_rows = _read_jsonl(oracles_path or _ORACLES)
    oracles: dict[str, dict] = {}
    for oracle in oracle_rows:
        tid = oracle.get("task_id")
        if not tid:
            raise CorpusError("an oracle row is missing task_id")
        if tid in oracles:
            raise CorpusError(f"duplicate oracle task_id {tid!r}")
        oracles[tid] = oracle

    specs: list[TaskSpec] = []
    seen: set[str] = set()
    for task in tasks:
        tid = task.get("task_id")
        if not tid:
            raise CorpusError("a task row is missing task_id")
        if tid in seen:
            raise CorpusError(f"duplicate task_id {tid!r}")
        seen.add(tid)

        leaks = _leak_terms(_agent_facing_text(task))
        if leaks:
            raise CorpusError(f"task {tid!r} agent-facing text leaks experiment terms: {leaks}")

        oracle = oracles.get(tid)
        if oracle is None:
            raise CorpusError(f"task {tid!r} has no oracle label in oracles.jsonl")

        harm_label = oracle.get("harm_label")
        if harm_label not in _VALID_HARM:
            raise CorpusError(f"task {tid!r} has invalid harm_label {harm_label!r}")
        scope = tuple(oracle.get("expected_edit_scope", ()))
        if not scope:
            raise CorpusError(f"task {tid!r} has empty expected_edit_scope")
        harm_type = oracle.get("harm_type", "none")
        must_fail = bool(oracle.get("oracle_build_must_fail", False))
        evaluator_test_project = oracle.get("evaluator_test_project")
        evaluator_test_filter = oracle.get("evaluator_test_filter")
        build_solution = oracle.get("build_solution", "TemplateBlueprint.sln")

        if harm_label == "risky" and not (must_fail or harm_type in _REAL_HARM_TYPES):
            raise CorpusError(f"risky task {tid!r} declares no real harm mechanism")
        if harm_type == "test_failure" and not evaluator_test_project:
            raise CorpusError(f"test_failure task {tid!r} must declare evaluator_test_project")
        if harm_label == "safe" and must_fail:
            raise CorpusError(f"safe task {tid!r} must not be expected to break the build")

        specs.append(TaskSpec(
            task_id=tid,
            description=task.get("description", ""),
            target_hints=tuple(task.get("target_hints", ())),
            harm_label=harm_label,
            expected_edit_scope=scope,
            harm_type=harm_type,
            oracle_build_must_fail=must_fail,
            evaluator_test_project=evaluator_test_project,
            evaluator_test_filter=evaluator_test_filter,
            build_solution=build_solution,
        ))
    return specs
