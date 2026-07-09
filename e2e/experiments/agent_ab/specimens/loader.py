"""Load all authored language specimens for the agent A/B experiment."""

from __future__ import annotations

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.specimens.csharp.corpus import loader as csharp_loader
from e2e.experiments.agent_ab.specimens.javascript.corpus import loader as javascript_loader


def load_corpus() -> list[TaskSpec]:
    specs = [*csharp_loader.load_corpus(), *javascript_loader.load_corpus()]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for spec in specs:
        if spec.task_id in seen:
            duplicates.add(spec.task_id)
        seen.add(spec.task_id)
    if duplicates:
        raise csharp_loader.CorpusError(
            f"duplicate task_id across specimens: {', '.join(sorted(duplicates))}"
        )
    return specs
