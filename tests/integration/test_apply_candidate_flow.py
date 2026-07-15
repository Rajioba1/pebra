from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pebra.adapters.candidate_binding import binding_for_patch
from pebra.adapters.candidate_replay_cache import CandidateReplayCache, CandidateReplayError
from pebra.adapters.repository_registry import RepositoryRegistry
from pebra.adapters.store.db import SqliteStore
from pebra.cli.main import main
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


_PATCH = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def test_apply_candidate_cli_uses_real_ledger_gate_cache_and_writer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/a.py").write_text("old\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@pebra.invalid")
    _git(repo, "config", "user.name", "PEBRA Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "seed")
    head = _git(repo, "rev-parse", "HEAD")
    metadata = RepositoryRegistry().resolve(str(repo))
    db = repo / ".pebra/pebra.db"
    store = SqliteStore(str(db))
    cache = CandidateReplayCache(repo / ".pebra/candidates")
    replay_metadata = cache.store({
        "request": {
            "task": "change a", "schema_version": "0.1", "thresholds": {}, "evidence": {},
            "candidate_actions": [{
                "id": "a1", "label": "change", "action_type": "edit",
                "proposed_patch": _PATCH, "expected_files": ["src/a.py"],
            }],
        },
        "trusted_candidate_verification": None,
        "trusted_task_obligations": {"required_files": ["src/a.py"]},
    })
    binding = binding_for_patch(repo, _PATCH)
    assert binding is not None

    def result(decision: Decision) -> AssessmentResult:
        return AssessmentResult(
            recommended_decision=decision,
            requires_confirmation=decision is not Decision.PROCEED,
            action_status=ActionStatus.PENDING,
            risk_mode=(
                RiskMode.ELEVATED_REVIEW if decision is Decision.ASK_HUMAN else RiskMode.NORMAL
            ),
            scores={"expected_loss": 0.3, "benefit": 0.2, "rau": -0.1},
            repo_id=metadata.repo_id,
            repo_root=metadata.repo_root,
            assessed_commit=head,
            model_guidance_packet={
                "guidance_packet_id": "gp_a1",
                "decision": decision.value,
                "binding": {
                    "safe_scope": {"files": ["src/a.py"]},
                    "candidate": binding,
                },
            },
        )

    request_payload = {"action_id": "a1", "candidate_replay": replay_metadata}
    store.persist_assessment(result(Decision.ASK_HUMAN), request_payload)
    authorized_id = store.persist_assessment(result(Decision.PROCEED), request_payload)
    store.close()

    assert main([
        "apply-candidate", "--assessment-id", authorized_id,
        "--repo-root", str(repo), "--db", str(db),
    ]) == 0

    assert (repo / "src/a.py").read_text(encoding="utf-8") == "new\n"
    with pytest.raises(CandidateReplayError, match="missing"):
        cache.load(replay_metadata)
