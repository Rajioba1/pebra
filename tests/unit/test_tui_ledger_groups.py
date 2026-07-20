"""Pure grouping rules for repeated Observatory ledger candidates."""

from __future__ import annotations

from typing import Any

from pebra.tui.ledger_groups import group_contiguous_assessments


_FP_A = "a" * 64
_FP_B = "b" * 64


def _row(assessment_id: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "assessment_id": assessment_id,
        "candidate_fingerprint": _FP_A,
        "assessed_commit": "abc1234",
        "decision": "proceed",
        "terminal_status": None,
        "task": "Fix authentication",
        "action_id": "edit-auth",
        "target_files": ["src/auth.py"],
        "scores": {"rau": 0.2, "expected_loss": 0.1, "benefit": 0.3},
    }
    row.update(overrides)
    return row


def test_identical_contiguous_bound_candidates_group() -> None:
    latest = _row("asm_3")
    older = _row("asm_2")

    groups = group_contiguous_assessments([latest, older, _row("asm_1", decision="reject")])

    assert groups[0].primary_assessment_id == "asm_3"
    assert groups[0].assessment_ids == ("asm_3", "asm_2")
    assert groups[0].latest_row is latest
    assert len(groups) == 2


def test_same_commit_and_decision_different_fingerprint_do_not_group() -> None:
    groups = group_contiguous_assessments(
        [_row("asm_2", candidate_fingerprint=_FP_A), _row("asm_1", candidate_fingerprint=_FP_B)]
    )

    assert [group.assessment_ids for group in groups] == [("asm_2",), ("asm_1",)]


def test_same_candidate_different_scores_do_not_group() -> None:
    groups = group_contiguous_assessments(
        [
            _row("asm_2"),
            _row("asm_1", scores={"rau": 0.2, "expected_loss": 0.1, "benefit": 0.4}),
        ]
    )

    assert [group.assessment_ids for group in groups] == [("asm_2",), ("asm_1",)]


def test_same_candidate_different_task_does_not_group() -> None:
    groups = group_contiguous_assessments(
        [_row("asm_2"), _row("asm_1", task="Fix authorization")]
    )

    assert [group.assessment_ids for group in groups] == [("asm_2",), ("asm_1",)]


def test_noncontiguous_repeat_does_not_cross_intervening_row() -> None:
    groups = group_contiguous_assessments(
        [_row("asm_3"), _row("asm_2", candidate_fingerprint=_FP_B), _row("asm_1")]
    )

    assert [group.assessment_ids for group in groups] == [
        ("asm_3",),
        ("asm_2",),
        ("asm_1",),
    ]


def test_legacy_unfingerprinted_rows_never_group() -> None:
    groups = group_contiguous_assessments(
        [_row("asm_2", candidate_fingerprint=None), _row("asm_1", candidate_fingerprint=None)]
    )

    assert [group.assessment_ids for group in groups] == [("asm_2",), ("asm_1",)]


def test_group_preserves_every_assessment_id_in_order() -> None:
    rows = [
        _row("asm_5"),
        _row("asm_4"),
        _row("asm_3", candidate_fingerprint=_FP_B),
        _row("asm_2", candidate_fingerprint=_FP_B),
        _row("asm_1", candidate_fingerprint="not-a-valid-fingerprint"),
    ]

    groups = group_contiguous_assessments(rows)

    assert tuple(assessment_id for group in groups for assessment_id in group.assessment_ids) == (
        "asm_5",
        "asm_4",
        "asm_3",
        "asm_2",
        "asm_1",
    )
    assert [group.latest_row for group in groups] == [rows[0], rows[2], rows[4]]
    assert rows[0]["assessment_id"] == "asm_5"
