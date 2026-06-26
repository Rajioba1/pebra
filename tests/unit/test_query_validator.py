"""Architecture §3 — query_validator (core, pure). Phase-2 minimal contract for explain/compare
queries (full schema lands with the MCP surfaces in a later phase)."""

from __future__ import annotations

import pytest

from pebra.core import query_validator as qv


def test_valid_query_passes() -> None:
    assert qv.validate_query({"assessment_id": "asm_1"}) == {"assessment_id": "asm_1"}


def test_missing_assessment_id_raises() -> None:
    with pytest.raises(qv.QueryValidationError):
        qv.validate_query({})


def test_empty_assessment_id_raises() -> None:
    with pytest.raises(qv.QueryValidationError):
        qv.validate_query({"assessment_id": ""})


def test_non_string_assessment_id_raises() -> None:
    # the id is a string everywhere downstream (store/outcome) — close the type at the gate
    for bad in (0, True, ["asm_1"], {"id": "x"}):
        with pytest.raises(qv.QueryValidationError):
            qv.validate_query({"assessment_id": bad})


def test_extra_keys_are_passed_through_unchanged() -> None:
    q = {"assessment_id": "asm_1", "date_range": "2026-06", "limit": 10}
    assert qv.validate_query(q) is q  # pass-through: nothing stripped (later phases read these)
