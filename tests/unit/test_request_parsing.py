"""Architecture §3.1, AD-8 — candidate_parser (raw dict -> AssessmentRequest) + request_validator."""

from __future__ import annotations

import pytest

from pebra.core import candidate_parser as cp
from pebra.core import request_validator as rv

_RAW = {
    "schema_version": "0.1",
    "task": "Fix failing login validation",
    "repo_id": "repo_local_example",
    "candidate_actions": [
        {
            "id": "a1",
            "label": "Patch validate_login only",
            "action_type": "edit",
            "affected_symbols": ["src/auth.py::validate_login"],
            "expected_files": ["src/auth.py"],
        }
    ],
    "evidence": {"p_success": 0.74},
    "thresholds": {"c3_max_expected_loss_without_human": 0.20},
}


def test_parse_builds_canonical_request() -> None:
    req = cp.parse(_RAW)
    assert req.task == "Fix failing login validation"
    assert req.schema_version == "0.1"
    assert len(req.candidate_actions) == 1
    a = req.candidate_actions[0]
    assert a.id == "a1"
    assert a.affected_symbols == ["src/auth.py::validate_login"]
    assert a.expected_files == ["src/auth.py"]
    assert req.evidence["p_success"] == 0.74
    assert req.thresholds["c3_max_expected_loss_without_human"] == 0.20


def test_validate_accepts_well_formed_request() -> None:
    req = cp.parse(_RAW)
    rv.validate(req)  # no raise


def test_validate_rejects_empty_task() -> None:
    bad = {**_RAW, "task": ""}
    with pytest.raises(rv.RequestValidationError):
        rv.validate(cp.parse(bad))


def test_validate_rejects_no_candidate_actions() -> None:
    bad = {**_RAW, "candidate_actions": []}
    with pytest.raises(rv.RequestValidationError):
        rv.validate(cp.parse(bad))


def test_validate_rejects_duplicate_action_ids() -> None:
    bad = {
        **_RAW,
        "candidate_actions": [
            {"id": "a1", "label": "x", "action_type": "edit"},
            {"id": "a1", "label": "y", "action_type": "edit"},
        ],
    }
    with pytest.raises(rv.RequestValidationError):
        rv.validate(cp.parse(bad))


def test_parse_missing_schema_version_defaults() -> None:
    raw = {k: v for k, v in _RAW.items() if k != "schema_version"}
    req = cp.parse(raw)
    assert req.schema_version  # defaulted, not blank
