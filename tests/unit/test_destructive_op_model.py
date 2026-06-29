"""Destructive-op slice, Phase 3 — pure event-injection model.

Only DELETE injects (call-graph question: who called the removed symbols). RENAME/MOVE are detected
upstream but inject NOTHING here (path-migration question, modeled later via blast). CREATE is a no-op.
p_event = baseline(arch/migration/schema) + fan_in_bonus(resolved rollup), capped. The no-graph
baseline (domain_entrypoint/migration/schema/anchor) is the outage blind-spot catch.
"""

from __future__ import annotations

import pytest

from pebra.core import destructive_op_model as dom
from pebra.core.models import ArchitectureEvidence, FileFanInRollup


def _rollup(pctl=0.0, resolved=False, callers=0):
    return FileFanInRollup(
        file_symbol_fanin_rollup_percentile=pctl,
        distinct_caller_count=callers,
        resolution_method="file_location" if resolved else "unresolved",
    )


def _arch(**over):
    return ArchitectureEvidence(**over)


def _events(op_kind, **kw):
    return dom.events_for_destructive_op(op_kind=op_kind, rollup=kw.pop("rollup", _rollup()),
                                         arch=kw.pop("arch", _arch()), **kw)


def _by(events, name):
    return next((e for e in events if e["event"] == name), None)


def test_create_injects_nothing():
    assert _events("CREATE") == []


def test_rename_injects_nothing_even_with_strong_signals():
    assert _events("RENAME", rollup=_rollup(0.99, resolved=True),
                   arch=_arch(domain_entrypoint=True)) == []


def test_move_injects_nothing():
    assert _events("MOVE", arch=_arch(domain_entrypoint=True)) == []


def test_delete_no_signals_injects_dependency_break_at_absolute_floor():
    events = _events("DELETE")
    dep = _by(events, "dependency_break")
    assert dep is not None
    assert dep["p_event"] == pytest.approx(0.03)
    assert dep["elicited_disutility"] == pytest.approx(0.60)


def test_delete_domain_entrypoint_baseline_is_the_outage_catch():
    # unresolved rollup (no graph) but an entrypoint -> still a meaningful p_event.
    events = _events("DELETE", arch=_arch(domain_entrypoint=True))
    assert _by(events, "dependency_break")["p_event"] == pytest.approx(0.15)


def test_delete_migration_baseline():
    events = _events("DELETE", is_migration=True)
    assert _by(events, "dependency_break")["p_event"] >= 0.20


def test_delete_schema_change_baseline():
    events = _events("DELETE", is_schema_change=True)
    assert _by(events, "dependency_break")["p_event"] >= 0.20


def test_delete_high_fanin_rollup_scales_p_event():
    events = _events("DELETE", rollup=_rollup(0.90, resolved=True))
    assert _by(events, "dependency_break")["p_event"] >= 0.25


def test_fan_in_bonus_zero_when_unresolved_even_if_percentile_set():
    # defensive: an unresolved rollup must contribute no fan-in bonus regardless of a stray percentile.
    events = _events("DELETE", rollup=_rollup(0.99, resolved=False))
    assert _by(events, "dependency_break")["p_event"] == pytest.approx(0.03)


def test_delete_combined_signals_capped():
    events = _events("DELETE", rollup=_rollup(0.95, resolved=True),
                     arch=_arch(domain_entrypoint=True))
    assert _by(events, "dependency_break")["p_event"] <= dom._P_EVENT_CAP


def test_delete_public_api_adds_public_api_break():
    events = _events("DELETE", is_public_api=True)
    assert _by(events, "public_api_break") is not None
    assert _by(events, "dependency_break") is not None


def test_delete_not_public_api_has_no_public_api_break():
    assert _by(_events("DELETE"), "public_api_break") is None
