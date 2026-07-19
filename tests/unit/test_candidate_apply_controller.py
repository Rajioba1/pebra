from __future__ import annotations

import contextlib

import pytest

from pebra.app import candidate_apply_controller as controller
from pebra.core.gate_contract import GatePermission, GateTier


_PATCH = "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
_META = {
    "status": "available",
    "algorithm": "sha256-candidate-replay-v1",
    "digest": "a" * 64,
}


class FakeStore:
    def __init__(self, *, valid=True):
        self.valid = valid

    def validate_chain(self):
        return self.valid

    def load_assessment(self, assessment_id):
        assert assessment_id == "asm_7"
        return {
            "repo_id": "repo-1",
            "request": {"candidate_replay": _META},
        }


class FakeReplay:
    def __init__(self):
        self.deleted = []
        self.consumed = []

    def load(self, metadata):
        assert metadata == _META
        return {
            "request": {
                "task": "change a",
                "candidate_actions": [{
                    "id": "a1", "label": "change", "action_type": "edit",
                    "proposed_patch": _PATCH, "expected_files": ["src/a.py"],
                }],
                "evidence": {}, "thresholds": {}, "schema_version": "0.1",
            },
            "trusted_candidate_verification": None,
            "trusted_task_obligations": {"required_files": ["src/a.py"]},
        }

    def delete(self, metadata):
        self.deleted.append(metadata)

    def consume(self, metadata):
        self.consumed.append(metadata)


class GateResult:
    def __init__(
        self,
        permission=GatePermission.CONTINUE,
        tier=GateTier.CONSULTED,
        matched="asm_7",
    ):
        self.permission = permission
        self.tier = tier
        self.matched_assessment_id = matched
        self.reason = "gate reason"


class FakeGate:
    def __init__(self, result=None):
        self.result = result or GateResult()
        self.calls = []

    def decide(self, event, *, db_path, consult_only):
        self.calls.append((event, db_path, consult_only))
        return self.result


class FakeApplier:
    def __init__(self):
        self.locked = False
        self.calls = []

    @contextlib.contextmanager
    def lock(self, repo_root):
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def apply(self, repo_root, patch, *, expected_files=None, acquire_lock=True):
        assert self.locked is True
        assert acquire_lock is False
        self.calls.append((repo_root, patch, expected_files))
        return ("src/a.py",)


def _apply(*, store=None, replay=None, gate=None, applier=None):
    return controller.apply_candidate(
        assessment_id="asm_7",
        repo_id="repo-1",
        repo_root="/repo",
        db_path="/repo/.pebra/pebra.db",
        store=store or FakeStore(),
        replay_cache=replay or FakeReplay(),
        gate=gate or FakeGate(),
        applier=applier or FakeApplier(),
    )


def test_apply_requires_valid_ledger_and_exact_consulted_assessment() -> None:
    with pytest.raises(controller.CandidateApplyError, match="ledger"):
        _apply(store=FakeStore(valid=False))

    for result in (
        GateResult(tier=GateTier.FAIL_OPEN),
        GateResult(tier=GateTier.PASS),
        GateResult(permission=GatePermission.RETURN_CANDIDATE),
        GateResult(matched="asm_8"),
    ):
        with pytest.raises(controller.CandidateApplyError, match="authorize"):
            _apply(gate=FakeGate(result))


def test_apply_authorizes_and_writes_inside_same_lock_then_deletes_replay() -> None:
    replay = FakeReplay()
    gate = FakeGate()
    applier = FakeApplier()

    outcome = _apply(replay=replay, gate=gate, applier=applier)

    assert outcome.assessment_id == "asm_7"
    assert outcome.changed_files == ("src/a.py",)
    event, db_path, consult_only = gate.calls[0]
    assert event == {
        "tool_name": "apply_patch",
        "cwd": "/repo",
        "tool_input": {"command": _PATCH},
    }
    assert db_path == "/repo/.pebra/pebra.db"
    assert consult_only is True
    assert replay.consumed == [_META]
    assert replay.deleted == [_META]


def test_apply_rejects_replay_for_different_repository() -> None:
    class OtherRepoStore(FakeStore):
        def load_assessment(self, assessment_id):
            value = super().load_assessment(assessment_id)
            value["repo_id"] = "repo-2"
            return value

    with pytest.raises(controller.CandidateApplyError, match="repository"):
        _apply(store=OtherRepoStore())


def test_failed_write_consumes_authorization_and_does_not_delete_tombstone() -> None:
    replay = FakeReplay()

    class FailingApplier(FakeApplier):
        def apply(self, repo_root, patch, *, expected_files=None, acquire_lock=True):
            assert self.locked is True
            raise OSError("write failed")

    with pytest.raises(OSError, match="write failed"):
        _apply(replay=replay, applier=FailingApplier())

    assert replay.consumed == [_META]
    assert replay.deleted == []
