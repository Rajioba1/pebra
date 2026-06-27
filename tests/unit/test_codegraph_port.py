"""M5c.5 — the CodeGraphProvider port is a structural Protocol: any object exposing
``fanin(action, repo_root) -> CodeGraphFanInEvidence`` satisfies it."""

from __future__ import annotations

from pebra.core.models import CandidateAction, CodeGraphFanInEvidence
from pebra.ports.codegraph_port import CodeGraphProvider


class _Fake:
    def fanin(self, action: CandidateAction, repo_root: str) -> CodeGraphFanInEvidence:
        return CodeGraphFanInEvidence(symbol_fan_in_percentile=0.5, resolution_method="location")


def test_fake_satisfies_protocol_at_runtime() -> None:
    provider: CodeGraphProvider = _Fake()
    ev = provider.fanin(CandidateAction(id="a1", label="l", action_type="edit"), "/repo")
    assert ev.symbol_fan_in_percentile == 0.5
    assert ev.resolution_method == "location"
