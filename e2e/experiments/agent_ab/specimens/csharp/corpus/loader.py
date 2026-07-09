"""Load the C# corpus using the shared specimen corpus validator."""

from __future__ import annotations

from pathlib import Path

from e2e.experiments.agent_ab.specimens import corpus_loader as _base

CorpusError = _base.CorpusError
_CORPUS_DIR = Path(__file__).resolve().parent


def load_corpus(tasks_path: Path | None = None, oracles_path: Path | None = None):
    return _base.load_corpus(
        tasks_path or _CORPUS_DIR / "tasks.jsonl",
        oracles_path or _CORPUS_DIR / "oracles.jsonl",
        specimen="csharp",
    )
