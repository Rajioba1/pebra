"""Unit tests for the Observatory query controller (Observatory TUI M1).

The controller centralizes the read projections/repo-scoping that the FastAPI dashboard and the Textual
TUI share, so the two surfaces cannot drift. It depends only on ObservatoryReadPort; these tests drive it
with an in-memory fake port (no SqliteStore, no FastAPI).
"""

from __future__ import annotations

from typing import Any

import pytest

from pebra.app import observatory_query_controller as oqc


def test_observatory_read_port_declares_shared_learning_read_methods() -> None:
    """M3: every Observatory surface receives learning reads through this port."""
    import inspect

    from pebra.ports import observatory_read_port as orp

    methods = {
        name for name, value in vars(orp.ObservatoryReadPort).items()
        if inspect.isfunction(value) and not name.startswith("_")
    }
    assert methods == {
        "assessment_facets", "list_assessments", "assessment_detail", "chain_status",
        "list_risk_snapshots", "list_learned_risk_facts", "assessment_prior_facets",
    }


class _FakePort:
    """Structural ObservatoryReadPort. Rows are returned as-is (already repo-scoped by the caller in
    production); limit/offset slicing mirrors the store so pass-through is observable."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        chain: dict[str, Any] | None = None,
        details: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._rows = rows or []
        self._chain = chain or {"valid": True, "counts": {}}
        self._details = details or {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def list_assessments(self, repo_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        self.calls.append(("list_assessments", (repo_id, limit, offset)))
        return self._rows[offset : offset + limit if limit else 0]

    def assessment_facets(self, repo_id: str):
        self.calls.append(("assessment_facets", (repo_id,)))
        return (
            {"decision": row["decision"], "terminal_status": row["terminal_status"]}
            for row in self._rows
        )

    def assessment_detail(self, assessment_id: str) -> dict[str, Any]:
        self.calls.append(("assessment_detail", (assessment_id,)))
        try:
            return self._details[assessment_id]
        except KeyError as exc:
            raise KeyError(f"no assessment {assessment_id!r}") from exc

    def chain_status(self) -> dict[str, Any]:
        self.calls.append(("chain_status", ()))
        return self._chain

    def list_risk_snapshots(self, repo_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self.calls.append(("list_risk_snapshots", (repo_id, limit)))
        return [{"snapshot_id": "rs_1", "status": "active", "metrics": {}}]

    def list_learned_risk_facts(
        self, repo_id: str, snapshot_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        self.calls.append(("list_learned_risk_facts", (repo_id, snapshot_id, limit)))
        return [{"fact_id": "lrf_1", "snapshot_id": "rs_1", "target_name": "p_success"}]

    def assessment_prior_facets(
        self, repo_id: str, assessment_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        self.calls.append(("assessment_prior_facets", (repo_id, tuple(assessment_ids))))
        return {assessment_id: {"source": "cold_start"} for assessment_id in assessment_ids}


def _row(**over: Any) -> dict[str, Any]:
    base = {
        "assessment_id": "asm_1",
        "decision": "proceed",
        "assessed_commit": "abc123",
        "terminal_status": None,
        "scores": {"rau": 0.2, "benefit": 0.4},
        "task": "Fix login",
        "action_id": "edit-auth",
        "declared_files": ["src/auth.py"],
        "bound_files": ["src/auth.py"],
        "target_files": ["src/auth.py"],
        "target_provenance": "candidate_bound",
        "candidate_fingerprint": "a" * 64,
    }
    base.update(over)
    return base


def test_list_assessments_delegates_and_forwards_limit_offset() -> None:
    rows = [_row(assessment_id=f"asm_{i}") for i in range(5)]
    port = _FakePort(rows)

    out = oqc.list_assessments("r", 2, 1, port=port)

    assert [r["assessment_id"] for r in out] == ["asm_1", "asm_2"]
    assert ("list_assessments", ("r", 2, 1)) in port.calls


def test_list_assessments_preserves_projected_identity_fields() -> None:
    projected = _row()
    port = _FakePort([projected])

    assert oqc.list_assessments("r", port=port)[0] == projected


def test_overview_counts_decisions_status_and_includes_chain() -> None:
    rows = [
        _row(decision="proceed", terminal_status=None),
        _row(decision="proceed", terminal_status="completed"),
        _row(decision="ask_human", terminal_status=None),
    ]
    port = _FakePort(rows, chain={"valid": True, "counts": {"assessments": 3}})

    out = oqc.overview("r", port=port)

    assert out["total"] == 3
    assert out["by_decision"] == {"proceed": 2, "ask_human": 1}
    assert list(out["by_decision"]) == ["proceed", "ask_human"]
    assert out["by_status"] == {"pending": 2, "completed": 1}  # None -> "pending"
    assert out["chain"] == {"valid": True, "counts": {"assessments": 3}}


def test_overview_counts_beyond_one_store_page() -> None:
    rows = [_row(assessment_id=f"asm_{i}") for i in range(501)]
    port = _FakePort(rows)

    out = oqc.overview("r", port=port)

    assert out["total"] == 501
    assert out["by_decision"] == {"proceed": 501}
    assert out["by_status"] == {"pending": 501}
    assert port.calls.count(("assessment_facets", ("r",))) == 1
    assert not any(name == "list_assessments" for name, _args in port.calls)


def test_scores_series_projects_only_series_keys_with_none_fill() -> None:
    rows = [
        _row(
            assessment_id="asm_9",
            scores={"rau": 0.2, "benefit": 0.4, "edit_confidence": 0.83, "not_a_series_key": 9},
        )
    ]
    port = _FakePort(rows)

    out = oqc.scores_series("r", 200, 0, port=port)

    item = out[0]
    assert item["assessment_id"] == "asm_9"
    assert item["decision"] == "proceed"
    assert item["assessed_commit"] == "abc123"
    assert item["terminal_status"] is None
    assert set(item["scores"]) == {
        "expected_loss", "benefit", "expected_utility", "rau", "edit_confidence",
    }
    assert item["scores"]["rau"] == 0.2
    assert item["scores"]["expected_loss"] is None  # absent -> None
    assert "not_a_series_key" not in item["scores"]


def test_scores_series_tolerates_null_scores() -> None:
    port = _FakePort([_row(scores=None)])
    out = oqc.scores_series("r", 200, 0, port=port)
    assert out[0]["scores"]["rau"] is None


def test_assessment_detail_returns_payload() -> None:
    detail = {"assessment_id": "asm_1", "content": {"repo_id": "r"}}
    port = _FakePort(details={"asm_1": detail})
    assert oqc.assessment_detail("asm_1", port=port) is detail


def test_assessment_detail_missing_raises_not_found() -> None:
    port = _FakePort()
    with pytest.raises(oqc.AssessmentNotFoundError):
        oqc.assessment_detail("asm_missing", port=port)


def test_assessment_detail_for_repo_returns_matching_repo() -> None:
    detail = {"assessment_id": "asm_1", "content": {"repo_id": "r"}}
    port = _FakePort(details={"asm_1": detail})
    assert oqc.assessment_detail_for_repo("asm_1", "r", port=port) is detail


def test_assessment_detail_for_repo_rejects_foreign_repo() -> None:
    detail = {"assessment_id": "asm_1", "content": {"repo_id": "other"}}
    port = _FakePort(details={"asm_1": detail})
    with pytest.raises(oqc.AssessmentNotFoundError):
        oqc.assessment_detail_for_repo("asm_1", "r", port=port)


def test_assessment_detail_for_repo_missing_raises_not_found() -> None:
    port = _FakePort()
    with pytest.raises(oqc.AssessmentNotFoundError):
        oqc.assessment_detail_for_repo("asm_missing", "r", port=port)


def test_assessment_detail_for_repo_treats_null_content_as_foreign() -> None:
    port = _FakePort(details={"asm_1": {"assessment_id": "asm_1", "content": None}})
    with pytest.raises(oqc.AssessmentNotFoundError):
        oqc.assessment_detail_for_repo("asm_1", "r", port=port)


def test_store_chain_status_delegates() -> None:
    port = _FakePort(chain={"valid": False, "counts": {"assessments": 7}})
    assert oqc.store_chain_status(port=port) == {"valid": False, "counts": {"assessments": 7}}


def test_learning_read_controller_delegates_without_reshaping() -> None:
    port = _FakePort()

    snapshots = oqc.learning_snapshots("r", 7, port=port)
    facts = oqc.learning_facts("r", "rs_1", 9, port=port)
    facets = oqc.assessment_prior_facets("r", ["asm_2", "asm_1"], port=port)

    assert snapshots == [{"snapshot_id": "rs_1", "status": "active", "metrics": {}}]
    assert facts == [{"fact_id": "lrf_1", "snapshot_id": "rs_1", "target_name": "p_success"}]
    assert facets == {"asm_2": {"source": "cold_start"}, "asm_1": {"source": "cold_start"}}
    assert ("list_risk_snapshots", ("r", 7)) in port.calls
    assert ("list_learned_risk_facts", ("r", "rs_1", 9)) in port.calls
    assert ("assessment_prior_facets", ("r", ("asm_2", "asm_1"))) in port.calls
