"""Production adapter exposing the universal gate through the application port."""

from __future__ import annotations

from typing import Any

from pebra.adapters import gate_check_adapter


class CandidateGateAdapter:
    def decide(
        self, event: dict[str, Any], *, db_path: str, consult_only: bool
    ) -> gate_check_adapter.GateDecision:
        return gate_check_adapter.decide(
            event, db_path=db_path, consult_only=consult_only
        )
