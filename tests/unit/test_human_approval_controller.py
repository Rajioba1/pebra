from __future__ import annotations

from types import SimpleNamespace

import pytest

from pebra.app import human_approval_controller as controller
from pebra.core.constants import Decision, RiskMode


_META = {
    "status": "available", "algorithm": "sha256-candidate-replay-v1", "digest": "a" * 64,
}
_BINDING = {
    "algorithm": "sha256-normalized-content-v1", "files": {"src/a.py": "b" * 64},
}


def _assessment(assessment_id, *, decision="ask_human", gate=2):
    replay_metadata = {
        **_META,
        "digest": ("a" if assessment_id == "asm_1" else "c") * 64,
    }
    return {
        "assessment_id": assessment_id,
        "repo_id": "repo-1",
        "decision": decision,
        "assessed_commit": "head-1",
        "scores": {"expected_loss": 0.4, "benefit": 0.3, "rau": -0.1},
        "gates_fired": [{
            "gate": gate,
            "name": {
                2: "c4_consequential_ask_human",
                3: "expected_loss_over_threshold",
                4: "negative_rau",
                9: "revision_has_no_credible_benefit",
            }.get(gate, "policy_violation"),
            **(
                {"expected_loss": 0.4, "threshold": 0.3} if gate == 3
                else {"rau": -0.1} if gate == 4
                else {"benefit": 0.0} if gate == 9
                else {}
            ),
        }],
        "model_guidance_packet": {
            "binding": {"candidate": _BINDING, "required_controls": ["review"]},
            "advisory": {"high_risk_triggers": [{"risk_class": "contract"}]},
        },
        "request": {
            "action_id": "a1", "candidate_replay": replay_metadata,
        },
    }


class FakeStore:
    def __init__(self, ids=("asm_1",), *, decision="ask_human", gate=2):
        self.rows = [
            _assessment(value, decision=decision, gate=gate) for value in ids
        ]

    def validate_chain(self):
        return True

    def pending_review_assessments(self, repo_id, head):
        assert (repo_id, head) == ("repo-1", "head-1")
        return self.rows

    def load_assessment(self, assessment_id):
        return next(row for row in self.rows if row["assessment_id"] == assessment_id)


class FakeReplay:
    def load(self, metadata):
        return {
            "request": {
                "task": "change a", "schema_version": "0.1", "thresholds": {}, "evidence": {},
                "candidate_actions": [{
                    "id": "a1", "label": "change", "action_type": "edit",
                    "proposed_patch": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n",
                    "expected_files": ["src/a.py"],
                }],
            },
            "trusted_candidate_verification": None,
            "trusted_task_obligations": {"required_files": ["src/a.py"]},
        }

    def delete(self, metadata):
        raise AssertionError("approval selection must not delete replay")

    def consume(self, metadata):
        raise AssertionError("approval selection must not consume replay")


def test_pending_approval_refuses_ambiguous_candidates() -> None:
    with pytest.raises(controller.HumanApprovalError, match="multiple"):
        controller.select_pending_approval(
            repo_id="repo-1", assessed_commit="head-1", store=FakeStore(("asm_1", "asm_2")),
            replay_cache=FakeReplay(),
        )


def test_pending_approval_can_select_explicit_assessment() -> None:
    pending = controller.select_pending_approval(
        repo_id="repo-1", assessed_commit="head-1", assessment_id="asm_2",
        store=FakeStore(("asm_1", "asm_2")), replay_cache=FakeReplay(),
    )

    assert pending.assessment_id == "asm_2"
    assert pending.summary["risk_benefit"] == {
        "expected_loss": 0.4, "benefit": 0.3, "expected_utility": None, "rau": -0.1,
    }
    assert pending.summary["files"] == ["src/a.py"]


def test_pending_approval_accepts_sanction_convertible_reject() -> None:
    pending = controller.select_pending_approval(
        repo_id="repo-1",
        assessed_commit="head-1",
        assessment_id="asm_1",
        store=FakeStore(decision="reject", gate=3),
        replay_cache=FakeReplay(),
    )

    assert pending.assessment_id == "asm_1"
    assert pending.summary["decision"] == "reject"
    assert pending.summary["controlling_gate"] == 3


def test_pending_approval_refuses_nonconvertible_policy_reject() -> None:
    with pytest.raises(controller.HumanApprovalError, match="not eligible for risk acceptance"):
        controller.select_pending_approval(
            repo_id="repo-1",
            assessed_commit="head-1",
            assessment_id="asm_1",
            store=FakeStore(decision="reject", gate=1),
            replay_cache=FakeReplay(),
        )


def test_pending_approval_refuses_malformed_exact_candidate_binding() -> None:
    store = FakeStore(decision="reject", gate=3)
    store.rows[0]["model_guidance_packet"]["binding"]["candidate"] = {}

    with pytest.raises(controller.HumanApprovalError, match="applicable candidate"):
        controller.select_pending_approval(
            repo_id="repo-1",
            assessed_commit="head-1",
            assessment_id="asm_1",
            store=store,
            replay_cache=FakeReplay(),
        )


def test_approval_revalidates_reject_eligibility_before_sanction(monkeypatch) -> None:
    store = FakeStore(decision="reject", gate=3)
    pending = controller.select_pending_approval(
        repo_id="repo-1",
        assessed_commit="head-1",
        assessment_id="asm_1",
        store=store,
        replay_cache=FakeReplay(),
    )
    store.rows[0]["gates_fired"] = [{"gate": 1, "name": "policy_violation"}]
    monkeypatch.setattr(
        controller.accept_risk_controller,
        "accept_risk",
        lambda *args, **kwargs: pytest.fail("ineligible rejection created a sanction"),
    )

    with pytest.raises(controller.HumanApprovalError, match="no longer eligible"):
        controller.approve_and_apply(
            pending,
            repo_id="repo-1",
            repo_root="/repo",
            db_path="/repo/.pebra/pebra.db",
            store=store,
            assess_ports={"sanction_port": object()},
            application_ports={
                "replay_cache": FakeReplay(), "gate": object(), "applier": object(),
            },
        )


def test_approval_revalidates_current_head_before_sanction(monkeypatch) -> None:
    store = FakeStore(decision="reject", gate=3)
    pending = controller.select_pending_approval(
        repo_id="repo-1",
        assessed_commit="head-1",
        assessment_id="asm_1",
        store=store,
        replay_cache=FakeReplay(),
    )
    monkeypatch.setattr(
        controller.accept_risk_controller,
        "accept_risk",
        lambda *args, **kwargs: pytest.fail("stale review created a sanction"),
    )

    with pytest.raises(controller.HumanApprovalError, match="HEAD changed"):
        controller.approve_and_apply(
            pending,
            repo_id="repo-1",
            repo_root="/repo",
            db_path="/repo/.pebra/pebra.db",
            store=store,
            assess_ports={"sanction_port": object(), "assessed_commit": "head-2"},
            application_ports={
                "replay_cache": FakeReplay(), "gate": object(), "applier": object(),
            },
        )


def test_approve_reassesses_controlled_risk_then_applies(monkeypatch) -> None:
    pending = controller.select_pending_approval(
        repo_id="repo-1", assessed_commit="head-1", store=FakeStore(),
        replay_cache=FakeReplay(),
    )
    sanctions = []
    monkeypatch.setattr(
        controller.accept_risk_controller,
        "accept_risk",
        lambda repo_id, spec, sanction_port: sanctions.append((repo_id, spec)) or "sx_1",
    )
    reassessed = SimpleNamespace(
        assessment_id="asm_2",
        recommended_result=SimpleNamespace(
            recommended_decision=Decision.PROCEED,
            risk_mode=RiskMode.CONTROLLED_HIGH_RISK,
        ),
    )
    monkeypatch.setattr(controller.assess_controller, "assess", lambda *a, **kw: reassessed)
    monkeypatch.setattr(
        controller.candidate_apply_controller,
        "apply_candidate",
        lambda **kw: SimpleNamespace(changed_files=("src/a.py",)),
    )
    assess_ports = {"sanction_port": object(), "assessed_commit": "head-1"}

    outcome = controller.approve_and_apply(
        pending,
        repo_id="repo-1", repo_root="/repo", db_path="/repo/.pebra/pebra.db",
        store=FakeStore(), assess_ports=assess_ports,
        application_ports={"replay_cache": FakeReplay(), "gate": object(), "applier": object()},
    )

    assert outcome.sanction_id == "sx_1"
    assert outcome.reassessment_id == "asm_2"
    assert outcome.changed_files == ("src/a.py",)
    assert sanctions[0][1]["risk_profile"] == {
        "assessment_id": "asm_1", "action_id": "a1", "candidate_binding": _BINDING,
    }
