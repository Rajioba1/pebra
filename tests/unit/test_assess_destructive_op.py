"""Destructive-op slice, Phase 5 — controller event injection (over fake ports).

Only DELETE injects; CREATE/RENAME/MOVE don't (RENAME/MOVE are recorded on the symbol_diff axis).
Covers the golden-safe no-op, the outage blind spot (deleted entrypoint with no graph), fan-in scaling,
and duplicate-event avoidance.
"""

from __future__ import annotations

import pytest

from pebra.app import assess_controller as ac
from pebra.core import models as m

_THRESHOLDS = {
    "max_expected_loss_without_human": 0.45, "c3_max_expected_loss_without_human": 0.20,
    "max_utility_sd_without_human": 0.20, "high_edit_confidence": 0.75, "low_edit_confidence": 0.50,
}
_BASE_EVENTS = [{"event": "test_regression", "p_event": 0.10, "elicited_disutility": 0.40}]


class _Ev:
    def __init__(self, events, arch=None):
        self._events, self._arch = events, arch or m.ArchitectureEvidence()

    def gather_evidence(self, request, action, repo_root):
        return m.EvidenceBundle(
            events=list(self._events), p_success=0.74, immediate_benefit=0.82, review_cost=0.12,
            criticality_stage="C3", criticality_value=0.80,
            edit_confidence_factors={"p_success": 0.74, "evidence_quality": 0.78,
                                     "testability": 0.80, "reversibility": 0.92,
                                     "source_reliability": 0.86, "scope_control": 0.92},
            thresholds=_THRESHOLDS,
            benefit_delta_evidence=m.BenefitDeltaEvidence(source_type="projected"),
            architecture_evidence=self._arch,
        )


class _SD:
    def __init__(
        self,
        kind="NONE",
        paths=(),
        visibility="internal",
        consequential=False,
        max_change_kind="BEHAVIORAL",
    ):
        self._kind, self._paths = kind, paths
        self._vis, self._cons = visibility, consequential
        self._max_change_kind = max_change_kind

    def symbol_diff(self, action, repo_root):
        return m.SymbolDiffEvidence(
            parsed_patch_available=True, changed_symbols=["src/config.py::x"],
            max_change_kind=self._max_change_kind, visibility=self._vis,
            consequential_symbol_changed=self._cons,
            file_operation_kind=self._kind, file_operation_paths=self._paths,
        )


class _FFI:
    def __init__(self, rollup):
        self._r = rollup

    def file_fanin_rollup(self, file_path, repo_root):
        return self._r


class _FI:
    def __init__(self, ev):
        self._ev = ev

    def fanin(self, action, repo_root):
        return self._ev


class _Blast:
    def blast(self, action, repo_root):
        return m.BlastEvidence()


class _Sanction:
    def active_sanction(self, repo_id, action):
        return None


def _bi(sd, ev, ffi=None, fanin=None):
    req = m.AssessmentRequest.single_action(
        task="t", action_id="a1", label="x", action_type="edit", expected_files=["src/config.py"],
    )
    return ac._build_input(
        req, req.candidate_actions[0], "r", "/x", _THRESHOLDS,
        evidence_provider=ev, symbol_diff_provider=sd, blast_provider=_Blast(),
        sanction_port=_Sanction(), fanin_provider=fanin, file_fanin_provider=ffi,
    )


def _ev_named(events, name):
    return next((e for e in events if e["event"] == name), None)


def test_no_destructive_op_is_inert():
    inp = _bi(_SD(kind="NONE"), _Ev(_BASE_EVENTS))
    assert _ev_named(inp.events, "dependency_break") is None
    assert len(inp.events) == len(_BASE_EVENTS)
    assert inp.file_fanin_rollup is None


def test_delete_injects_dependency_break():
    inp = _bi(_SD(kind="DELETE", paths=("src/config.py",)),
              _Ev(_BASE_EVENTS, arch=m.ArchitectureEvidence(domain_entrypoint=True)),
              ffi=_FFI(m.FileFanInRollup()))  # unresolved
    assert _ev_named(inp.events, "dependency_break") is not None
    assert inp.file_fanin_rollup is not None


def test_delete_outage_baseline_without_provider():
    # deleted entrypoint, NO file_fanin_provider (graph blind) -> baseline floor still fires.
    inp = _bi(_SD(kind="DELETE", paths=("src/config.py",)),
              _Ev(_BASE_EVENTS, arch=m.ArchitectureEvidence(domain_entrypoint=True)), ffi=None)
    dep = _ev_named(inp.events, "dependency_break")
    assert dep is not None
    assert dep["p_event"] == pytest.approx(0.15)
    assert inp.file_fanin_rollup.resolution_method == "unresolved"


def test_create_does_not_inject():
    inp = _bi(_SD(kind="CREATE", paths=("src/new.py",)), _Ev(_BASE_EVENTS))
    assert _ev_named(inp.events, "dependency_break") is None
    assert inp.file_fanin_rollup is None


def test_rename_recorded_but_not_injected():
    inp = _bi(_SD(kind="RENAME", paths=("src/foo.py",)),
              _Ev(_BASE_EVENTS, arch=m.ArchitectureEvidence(domain_entrypoint=True)))
    assert _ev_named(inp.events, "dependency_break") is None       # not scored
    assert inp.file_fanin_rollup is None
    assert inp.symbol_diff_evidence.file_operation_kind == "RENAME"  # but recorded


def test_high_fanin_delete_has_higher_p_event_than_blind():
    high = _bi(_SD(kind="DELETE", paths=("src/config.py",)), _Ev(_BASE_EVENTS),
               ffi=_FFI(m.FileFanInRollup(file_symbol_fanin_rollup_percentile=0.95,
                                          resolution_method="file_location")))
    blind = _bi(_SD(kind="DELETE", paths=("src/config.py",)), _Ev(_BASE_EVENTS),
                ffi=_FFI(m.FileFanInRollup()))
    assert _ev_named(high.events, "dependency_break")["p_event"] > \
        _ev_named(blind.events, "dependency_break")["p_event"]


def test_internal_high_fanin_delete_does_not_inject_public_api_break():
    # consequential_symbol_changed means HIGH INTERNAL fan-in, NOT exported API. Deleting a high-fan-in
    # INTERNAL symbol must inject dependency_break but NOT public_api_break (which would over-inflate loss).
    inp = _bi(_SD(kind="DELETE", paths=("src/config.py",), visibility="internal", consequential=True),
              _Ev(_BASE_EVENTS))
    assert _ev_named(inp.events, "dependency_break") is not None
    assert _ev_named(inp.events, "public_api_break") is None


def test_public_api_delete_injects_public_api_break():
    inp = _bi(_SD(kind="DELETE", paths=("src/config.py",), visibility="public_api"), _Ev(_BASE_EVENTS))
    assert _ev_named(inp.events, "public_api_break") is not None


def test_existing_dependency_break_not_duplicated():
    base = _BASE_EVENTS + [{"event": "dependency_break", "p_event": 0.25, "elicited_disutility": 0.6}]
    inp = _bi(_SD(kind="DELETE", paths=("src/config.py",)),
              _Ev(base, arch=m.ArchitectureEvidence(domain_entrypoint=True)), ffi=None)
    deps = [e for e in inp.events if e["event"] == "dependency_break"]
    assert len(deps) == 1
    assert deps[0]["p_event"] == 0.25  # the pre-existing one is kept, injected one skipped


def test_existing_weak_dependency_break_is_raised_by_delete_signal():
    base = _BASE_EVENTS + [{"event": "dependency_break", "p_event": 0.01, "elicited_disutility": 0.2}]
    inp = _bi(_SD(kind="DELETE", paths=("src/config.py",)),
              _Ev(base, arch=m.ArchitectureEvidence(domain_entrypoint=True)), ffi=None)

    deps = [e for e in inp.events if e["event"] == "dependency_break"]

    assert len(deps) == 1
    assert deps[0]["p_event"] == pytest.approx(0.15)
    assert deps[0]["elicited_disutility"] == pytest.approx(0.60)


def test_high_fanin_contract_modify_injects_dependency_break():
    inp = _bi(
        _SD(kind="NONE", max_change_kind="CONTRACT", consequential=False),
        _Ev(_BASE_EVENTS),
        fanin=_FI(m.FanInEvidence(symbol_fan_in_percentile=0.95, symbol_caller_count=13,
                                  resolution_method="location", graph_freshness="fresh")),
    )

    dep = _ev_named(inp.events, "dependency_break")

    assert dep is not None
    assert dep["p_event"] > 0.20
    assert inp.file_fanin_rollup is None


def test_public_contract_modify_injects_public_api_break():
    inp = _bi(
        _SD(kind="NONE", visibility="public_api", max_change_kind="CONTRACT"),
        _Ev(_BASE_EVENTS),
        fanin=_FI(m.FanInEvidence(symbol_fan_in_percentile=0.95, symbol_caller_count=13,
                                  resolution_method="location", graph_freshness="fresh")),
    )

    assert _ev_named(inp.events, "public_api_break") is not None


def test_untrusted_modify_graph_does_not_inject_dependency_break():
    inp = _bi(
        _SD(kind="NONE", max_change_kind="CONTRACT"),
        _Ev(_BASE_EVENTS),
        fanin=_FI(m.FanInEvidence(symbol_fan_in_percentile=0.95, symbol_caller_count=13,
                                  resolution_method="unresolved", graph_freshness="unknown")),
    )

    assert _ev_named(inp.events, "dependency_break") is None
    assert _ev_named(inp.events, "public_api_break") is None
    assert _ev_named(inp.events, "api_contract_break") is None
