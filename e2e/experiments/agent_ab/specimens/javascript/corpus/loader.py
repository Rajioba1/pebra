"""Load the JavaScript/TypeScript (Zod) corpus using the shared, language-agnostic validator.

The validation logic lives in the (legacy-located) csharp corpus loader; it is language-neutral (JSON +
leak-scan + harm/label/profile checks). This module only binds it to the JS corpus files. TODO: promote
the shared loader to the framework root so neither specimen imports the other.
"""

from __future__ import annotations

from pathlib import Path

from e2e.experiments.agent_ab.specimens.csharp.corpus import loader as _base

CorpusError = _base.CorpusError
_CORPUS_DIR = Path(__file__).resolve().parent


def load_corpus(tasks_path: Path | None = None, oracles_path: Path | None = None):
    return _base.load_corpus(
        tasks_path or _CORPUS_DIR / "tasks.jsonl",
        oracles_path or _CORPUS_DIR / "oracles.jsonl",
        specimen="javascript",
        default_repo_identity_files=("package.json", "pnpm-lock.yaml"),
    )
