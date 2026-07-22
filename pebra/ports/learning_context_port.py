"""Read/write contract for PEBRA-owned historical learning context."""

from __future__ import annotations

from typing import Protocol

from pebra.core.learning_context import LearningContextEntry, LearningContextRecall


class LearningContextPort(Protocol):
    def materialize_learning_context(self, assessment_id: str) -> LearningContextEntry | None: ...

    def recall_learning_context(self, repo_id: str, query: str, *, byte_limit: int = 4096) -> LearningContextRecall: ...
