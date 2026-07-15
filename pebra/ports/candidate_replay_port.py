"""Port for integrity-checked, ephemeral candidate replay inputs."""

from __future__ import annotations

from typing import Any, Protocol


class CandidateReplayPort(Protocol):
    def store(self, bundle: dict[str, Any]) -> dict[str, Any]: ...

    def load(self, metadata: dict[str, Any]) -> dict[str, Any]: ...

    def consume(self, metadata: dict[str, Any]) -> None: ...

    def delete(self, metadata: dict[str, Any]) -> None: ...
