"""Phase 5 — experiment write-gate + metric fixes (E1/E3/E4/E5).

The treatment arm routes write_file through the SAME `pebra gate-check` decision the product uses, so
the A/B measures the must-consult intervention. Invariants under test:
- write_file returns the SAME schema in both arms ({"ok", "blocked"}); only the value differs (E1).
- a gate deny blocks the write (no file written) and carries a reason; a gate error fails OPEN (E1/E3).
- a BLOCKED write is not counted as a real edit by the adherence heeded-proxy (E4) or the oracle's
  edit-cycle count (E5) — both would otherwise bias treatment.
No `import pebra` here: the gate is reached via an injected backend (subprocess in production).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from e2e.experiments.agent_ab.metrics import adherence, oracle
from e2e.experiments.agent_ab.models import ToolCallRecord
from e2e.experiments.agent_ab.runners import agent_loop
from e2e.utils import cli_harness


def _rec(seq: int, name: str, result: dict) -> ToolCallRecord:
    return ToolCallRecord(sequence=seq, name=name, arguments={}, result=result)


# ---- E1/E3: gated write_file ---------------------------------------------------------------

_WRITE_KEYS = {"ok", "blocked", "reason"}


def test_gated_write_allows_and_writes(tmp_path):
    setup = SimpleNamespace(repo_path=tmp_path, gate_check_backend=lambda ev: {"permission": "allow"})
    r = agent_loop._gated_write({"path": "a.cs", "content": "hi"}, setup)
    assert r == {"ok": True, "blocked": False, "reason": None}
    assert (tmp_path / "a.cs").read_text(encoding="utf-8") == "hi"


def test_gated_write_denies_and_does_not_write(tmp_path):
    setup = SimpleNamespace(repo_path=tmp_path,
                            gate_check_backend=lambda ev: {"permission": "deny", "reason": "consult first"})
    r = agent_loop._gated_write({"path": "a.cs", "content": "hi"}, setup)
    assert r == {"ok": False, "blocked": True, "reason": "consult first"}
    assert not (tmp_path / "a.cs").exists()  # blocked -> nothing written


def test_gated_write_identical_key_set_both_outcomes(tmp_path):
    # The key set must be INVARIANT across arms/outcomes (== not subset) — else the agent could infer
    # its arm from the write-result shape. Only values differ.
    allow = agent_loop._gated_write({"path": "a.cs", "content": "x"},
                                    SimpleNamespace(repo_path=tmp_path,
                                                    gate_check_backend=lambda ev: {"permission": "allow"}))
    deny = agent_loop._gated_write({"path": "b.cs", "content": "x"},
                                   SimpleNamespace(repo_path=tmp_path,
                                                   gate_check_backend=lambda ev: {"permission": "deny"}))
    assert set(allow) == set(deny) == _WRITE_KEYS


def test_gated_write_error_has_same_key_set(tmp_path):
    # A write ERROR (path traversal) must ALSO carry the invariant key set — no arm-distinguishing shape.
    setup = SimpleNamespace(repo_path=tmp_path, gate_check_backend=lambda ev: {"permission": "allow"})
    r = agent_loop._gated_write({"path": "../escape.cs", "content": "x"}, setup)
    assert set(r) == _WRITE_KEYS and r["ok"] is False and r["blocked"] is False and r["reason"]


def test_gated_write_fails_open_on_backend_error(tmp_path):
    def boom(ev):
        raise RuntimeError("gate down")
    setup = SimpleNamespace(repo_path=tmp_path, gate_check_backend=boom)
    r = agent_loop._gated_write({"path": "a.cs", "content": "hi"}, setup)
    assert r == {"ok": True, "blocked": False, "reason": None}  # broken gate must never block the write
    assert (tmp_path / "a.cs").read_text(encoding="utf-8") == "hi"


def test_incompatible_gate_contract_aborts_without_writing(tmp_path):
    def incompatible(_event):
        raise cli_harness.GateContractError("unsupported gate contract schema")

    setup = SimpleNamespace(repo_path=tmp_path, gate_check_backend=incompatible)
    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        agent_loop._gated_write({"path": "a.cs", "content": "hi"}, setup)

    assert not (tmp_path / "a.cs").exists()


def test_invalid_gate_json_aborts_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli_harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )
    setup = SimpleNamespace(
        repo_path=tmp_path,
        gate_check_backend=lambda event: cli_harness.gate_check(
            event, db=tmp_path / "pebra.db", consult_only=True
        ),
    )

    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        agent_loop._gated_write({"path": "a.cs", "content": "hi"}, setup)

    assert not (tmp_path / "a.cs").exists()


@pytest.mark.parametrize("permission", ("deny", "ask"))
def test_held_candidate_never_writes_or_attributes_assessment(tmp_path, permission):
    attributed = []
    setup = SimpleNamespace(
        repo_path=tmp_path,
        gate_check_backend=lambda _event: {
            "permission": permission,
            "tier": "consulted_review" if permission == "ask" else "consulted_revise",
            "reason": "This exact candidate is held—not your requested goal.",
            "matched_assessment_id": "asm_exact",
        },
        write_applied_backend=lambda decision: attributed.append(decision),
    )

    result = agent_loop._gated_write({"path": "a.cs", "content": "hi"}, setup)

    assert result["blocked"] is True
    assert not (tmp_path / "a.cs").exists()
    assert attributed == []


# ---- E4: adherence heeded-proxy ignores blocked writes -------------------------------------

def test_inspect_first_heeded_ignores_blocked_write():
    # advisory=inspect_first; a BLOCKED write precedes the build; the REAL write comes after the build.
    calls = [
        _rec(0, "advisory_check", {"recommended_decision": "inspect_first"}),
        _rec(1, "write_file", {"ok": False, "blocked": True}),  # blocked -> not a real edit
        _rec(2, "run_build", {"passed": True}),
        _rec(3, "write_file", {"ok": True, "blocked": False}),  # real edit, AFTER the build
    ]
    _, decision, heeded, _ = adherence.classify(calls, primary_file="a.cs", modified_files=["a.cs"])
    assert decision == "inspect_first" and heeded is True  # verified before the first REAL write


# ---- E5: oracle edit-cycle count ignores blocked writes -----------------------------------

def test_edit_cycles_ignores_blocked_write():
    result = SimpleNamespace(tool_calls=[
        _rec(0, "write_file", {"ok": False, "blocked": True}),  # blocked -> no pending edit
        _rec(1, "run_build", {"passed": True}),                 # would be a spurious cycle under old code
        _rec(2, "write_file", {"ok": True, "blocked": False}),  # real edit
        _rec(3, "run_build", {"passed": True}),                 # the one real write->build cycle
    ])
    assert oracle._edit_cycles(result) == 1
