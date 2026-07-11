"""Controller/store/engine proof for revision-envelope continuity."""

from dataclasses import replace

from pebra.adapters.store.db import SqliteStore
from pebra.app import assess_controller
from pebra.core import models
from pebra.core.constants import Decision
from tests.unit.test_assess_controller import (
    FakeBlast,
    FakeEvidence,
    FakeRegistry,
    FakeSanction,
)


class _SequencedEvidence:
    def __init__(self) -> None:
        self.calls = 0

    def gather_evidence(self, request, action, repo_root):
        base = FakeEvidence().gather_evidence(request, action, repo_root)
        self.calls += 1
        if self.calls == 1:
            return replace(
                base,
                events=[{
                    "event": "public_api_break",
                    "p_event": 0.60,
                    "elicited_disutility": 0.80,
                }],
                immediate_benefit=1.0,
            )
        return base


class _SequencedSymbolDiff:
    def __init__(self) -> None:
        self.calls = 0

    def symbol_diff(self, action, repo_root):
        self.calls += 1
        return models.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=(
                ["pkg.oldName", "pkg.newName"] if self.calls == 1 else ["pkg.newName"]
            ),
            max_change_kind="CONTRACT" if self.calls == 1 else "BEHAVIORAL",
            visibility="public_api",
            consequential_symbol_changed=self.calls == 1,
        )


def _request(expected_files: list[str]) -> models.AssessmentRequest:
    return models.AssessmentRequest.single_action(
        task="Rename the public function while preserving compatibility",
        action_id="stable-action",
        label="compatibility rename",
        expected_files=expected_files,
        proposed_patch="--- a/src/api.ts\n+++ b/src/api.ts\n@@ -1 +1 @@\n-old\n+new\n",
    )


def test_low_risk_partial_revision_cannot_proceed_after_risky_origin(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "pebra.db"))
    evidence = _SequencedEvidence()
    symbols = _SequencedSymbolDiff()
    common = {
        "thresholds": {
            "max_expected_loss_without_human": 0.45,
            "c3_max_expected_loss_without_human": 0.20,
            "max_revise_safer_attempts": 1,
        },
        "start_path": "/abs/path/to/example-repo/src",
        "evidence_provider": evidence,
        "symbol_diff_provider": symbols,
        "blast_provider": FakeBlast(),
        "sanction_port": FakeSanction(),
        "repository_registry": FakeRegistry(),
        "store": store,
        "assessed_commit": "abc123",
    }

    first = assess_controller.assess(
        _request(["src/api.ts", "src/compat.ts"]),
        **common,
    )
    second = assess_controller.assess(_request(["src/api.ts"]), **common)

    assert first.recommended_result.recommended_decision is Decision.REVISE_SAFER
    assert second.recommended_result.recommended_decision is Decision.ASK_HUMAN
    gate = next(
        item
        for item in second.recommended_result.gates_fired
        if item["name"] == "revision_envelope_incomplete"
    )
    assert gate["missing_files"] == ["src/compat.ts"]
    assert gate["missing_public_symbols"] == ["pkg.oldName"]
