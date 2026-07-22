"""Milestone 0 — Observatory learning baseline + characterization lock.

This module locks three things before any production code changes for the Observable-Learning plan:

1. A GO/NO-GO capability gate: the Python SQLite runtime PEBRA uses MUST support FTS5 + bm25().
   The whole `learning_context` recall design (Milestone 5) depends on it. If a supported CI leg
   lacks FTS5 this test fails there, halting the plan rather than silently shipping a
   platform-specific recall path.
2. An honest empty-learning baseline: a fresh store has no promoted local learning. Absence is a
   legitimate fixture condition (cold start), not a product failure.
3. A reusable fixture taxonomy for the learning read model that Milestones 3-5 will consume.

Forward-looking assertions for not-yet-built behavior are `xfail(strict=True)` so they become the
executable spec for their milestone: when it lands, the test XPASSes and strict xfail fails, forcing
removal of the marker.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from pebra.adapters.store.db import SqliteStore

_REPO = "repo_learning_fixture"


# --- 1. GO/NO-GO: FTS5 + bm25 capability gate ----------------------------------------------

def test_sqlite_runtime_supports_fts5_and_bm25() -> None:
    """Milestone 5 recall requires FTS5 with the bm25() ranking function. Hard-stop the plan on any
    CI leg where this fails; do not degrade to a platform-specific recall path."""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(body)")
        con.execute("INSERT INTO _fts_probe(body) VALUES ('safe edit lesson recall')")
        rows = con.execute(
            "SELECT body, bm25(_fts_probe) FROM _fts_probe WHERE _fts_probe MATCH 'lesson' "
            "ORDER BY bm25(_fts_probe)"
        ).fetchall()
        assert rows and rows[0][0] == "safe edit lesson recall"
        assert isinstance(rows[0][1], float)  # bm25() returns a rank score
    except sqlite3.OperationalError as exc:  # pragma: no cover - only on an FTS5-less runtime
        pytest.fail(f"SQLite FTS5/bm25 unavailable on this runtime — plan hard stop: {exc}")
    finally:
        con.close()


# --- 2 & 3. Fixture taxonomy for the learning read model -----------------------------------
# States the Milestone 3-5 read model must classify. `shipped` and `mixed` priors additionally
# depend on the packaged shipped-prior bundle (not a store row); those are constructed in the
# Milestone 3 read-model fixtures where source classification is actually exercised. Here we lock
# the store-side states the batch/append APIs already support.

def _metrics(**over: Any) -> dict[str, Any]:
    base = {"promotion_reason": "observatory_learning_fixture", "hash_version": 2}
    base.update(over)
    return base


def _fact(**over: Any) -> dict[str, Any]:
    base = {
        "target_type": "risk_binary", "target_name": "p_success", "scope_kind": "global",
        "scope_value": "", "specificity_rank": 0, "scope_json": {},
        "fact_json": {
            "value": 0.42, "weight": 1.0, "sample_size": 150,
            "calibration_method": "observed_rate_v1", "calibration_quality": 1.0,
            "scope_change_count": 0, "provider_version": None, "index_version": None,
        },
        "fact_type": "learned_override", "status": "active",
        "requires_human_ratification": False,
    }
    base.update(over)
    return base


def _cold_start_store(tmp_path) -> SqliteStore:
    """No promoted snapshots and no learned facts — the honest default of a fresh repo."""
    return SqliteStore(str(tmp_path / "cold.db"))


def _local_learned_store(tmp_path) -> SqliteStore:
    """One active promoted snapshot with a learned fact — a genuine local-learned prior."""
    store = SqliteStore(str(tmp_path / "local.db"))
    store.insert_learned_fact_batch_with_snapshot(_REPO, _metrics(), [_fact()], snapshot_status="active")
    return store


def _shadow_only_store(tmp_path) -> SqliteStore:
    """A shadow snapshot exists but nothing is promoted/active — still cold from the gate's view."""
    store = SqliteStore(str(tmp_path / "shadow.db"))
    store.insert_risk_snapshot(_REPO, _metrics(), status="shadow")
    return store


def test_cold_start_baseline_has_no_promoted_local_learning(tmp_path) -> None:
    """Honest baseline: absence of promoted learning is a fixture condition, not a failure."""
    store = _cold_start_store(tmp_path)
    try:
        assert store.list_risk_snapshots(_REPO) == []
        assert store.list_learned_risk_facts(_REPO) == []
    finally:
        store.close()


def test_local_learned_fixture_exposes_snapshot_and_fact(tmp_path) -> None:
    store = _local_learned_store(tmp_path)
    try:
        snapshots = store.list_risk_snapshots(_REPO)
        facts = store.list_learned_risk_facts(_REPO)
        assert len(snapshots) == 1
        assert len(facts) == 1 and facts[0]["target_name"] == "p_success"
        assert store.validate_learning_chains() is True
    finally:
        store.close()


def test_shadow_only_fixture_is_not_active_learning(tmp_path) -> None:
    store = _shadow_only_store(tmp_path)
    try:
        # A shadow snapshot is recorded but no fact is promoted — the "learned" gate signal is absent.
        assert store.list_learned_risk_facts(_REPO) == []
        assert len(store.list_risk_snapshots(_REPO)) == 1
    finally:
        store.close()


# --- Forward-looking: the learning_context recall store arrives in Milestone 5 --------------

@pytest.mark.xfail(strict=True, reason="Milestone 5A: learning_context module not implemented yet")
def test_learning_context_module_exists() -> None:
    import importlib

    mod = importlib.import_module("pebra.core.learning_context")
    assert hasattr(mod, "build_learning_context")  # deterministic per-outcome lesson builder
