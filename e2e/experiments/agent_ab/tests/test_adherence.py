from __future__ import annotations

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import adherence
from e2e.experiments.agent_ab.models import ToolCallRecord

PRIMARY = "src/A.cs"


def _adv(decision):
    return ToolCallRecord(0, "advisory_check", {}, {"recommended_decision": decision})


def _write(seq, path=PRIMARY, *, ok=True):
    # realistic normalized write result ({"ok", "blocked"}); a gate-blocked write is ok=False.
    return ToolCallRecord(seq, "write_file", {"path": path}, {"ok": ok, "blocked": not ok})


def _build(seq):
    return ToolCallRecord(seq, "run_build", {}, {})


def test_no_call_is_did_not_call():
    called, decision, heeded, state = adherence.classify([_write(1)], primary_file=PRIMARY,
                                                          modified_files=[PRIMARY])
    assert called is False and decision is None and heeded is None
    assert state == models.ADH_DID_NOT_CALL


def test_reject_not_modified_is_heeded():
    calls = [_adv("reject")]
    _, _, heeded, state = adherence.classify(calls, primary_file=PRIMARY, modified_files=[])
    assert heeded is True and state == models.ADH_HEEDED


def test_reject_but_modified_is_ignored():
    calls = [_adv("reject"), _write(1)]
    _, _, heeded, state = adherence.classify(calls, primary_file=PRIMARY, modified_files=[PRIMARY])
    assert heeded is False and state == models.ADH_IGNORED


def test_inspect_first_build_before_write_is_heeded():
    calls = [_adv("inspect_first"), _build(1), _write(2)]
    _, _, heeded, state = adherence.classify(calls, primary_file=PRIMARY, modified_files=[PRIMARY])
    assert heeded is True and state == models.ADH_HEEDED


def test_inspect_first_write_without_build_is_ignored():
    calls = [_adv("inspect_first"), _write(1)]
    _, _, heeded, state = adherence.classify(calls, primary_file=PRIMARY, modified_files=[PRIMARY])
    assert heeded is False and state == models.ADH_IGNORED


def test_proceed_is_no_restriction():
    _, _, heeded, state = adherence.classify([_adv("proceed"), _write(1)], primary_file=PRIMARY,
                                             modified_files=[PRIMARY])
    assert heeded is None and state == models.ADH_NO_RESTRICTION


def test_sham_null_decision_is_no_restriction():
    _, decision, heeded, state = adherence.classify([_adv(None), _write(1)], primary_file=PRIMARY,
                                                     modified_files=[PRIMARY])
    assert decision is None and heeded is None and state == models.ADH_NO_RESTRICTION


def test_norm_only_removes_explicit_current_dir_prefix():
    assert adherence._norm("./src/A.cs") == "src/A.cs"  # noqa: SLF001
    assert adherence._norm("../src/A.cs") == "../src/A.cs"  # noqa: SLF001
    assert adherence._norm(".hidden/A.cs") == ".hidden/A.cs"  # noqa: SLF001
