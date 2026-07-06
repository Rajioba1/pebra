"""N-arm assay runner: arm sets, per-arm backend dispatch, and run_trial fan-out.

prepare_arm/_invoke_subject_agent (real clone/graph/build/LLM) are monkeypatched so these stay pure.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.runners import run_pair
from e2e.experiments.agent_ab.tools import advisory_blast_radius, advisory_check_real, advisory_check_sham
from e2e.utils import cli_harness


def test_arms_for_risky_and_safe():
    assert run_pair.arms_for("risky") == (
        models.ARM_SHAM, models.ARM_ORACLE_POSITIVE, models.ARM_ENFORCED_CONTROL,
        models.ARM_BLAST_RADIUS, models.ARM_PEBRA)
    assert run_pair.arms_for("safe") == (models.ARM_SHAM, models.ARM_BLAST_RADIUS, models.ARM_PEBRA)
    assert models.ARM_ORACLE_POSITIVE not in run_pair.arms_for("safe")  # no harm to fix on safe tasks


def _mark(name):
    return lambda *a, **k: {"backend": name}


def test_advisory_backend_dispatch(monkeypatch):
    monkeypatch.setattr(advisory_check_real, "advise", _mark("real"))
    monkeypatch.setattr(advisory_check_sham, "advise", _mark("sham"))
    monkeypatch.setattr(advisory_blast_radius, "advise", _mark("blast"))

    def which(arm):
        return run_pair._advisory_backend(arm, Path("/r"), Path("/d"))({"x": 1})["backend"]

    assert which(models.ARM_PEBRA) == "real"
    assert which(models.ARM_TREATMENT) == "real"          # legacy treatment maps to real
    assert which(models.ARM_BLAST_RADIUS) == "blast"
    assert which(models.ARM_SHAM) == "sham"
    assert which(models.ARM_ENFORCED_CONTROL) == "sham"
    assert which(models.ARM_ORACLE_POSITIVE) == "sham"    # oracle uses sham advisory (mechanism = pre-patch)
    assert which(models.ARM_CONTROL) == "sham"


def test_gate_backend_only_pebra_enforces(monkeypatch):
    monkeypatch.setattr(cli_harness, "gate_check",
                        lambda event, *, db, consult_only: {"permission": "deny"})

    def perm(arm):
        return run_pair._gate_check_backend(arm, Path("/d"))({})["permission"]

    assert perm(models.ARM_PEBRA) == "deny" and perm(models.ARM_TREATMENT) == "deny"
    assert perm(models.ARM_ENFORCED_CONTROL) == "deny"
    for arm in (models.ARM_SHAM, models.ARM_BLAST_RADIUS, models.ARM_ORACLE_POSITIVE):
        assert perm(arm) == "allow"  # non-PEBRA arms never block a write


def _stub_runner(monkeypatch):
    monkeypatch.setattr(run_pair.rs, "prepare_external_repo", lambda: object())
    monkeypatch.setattr(run_pair, "prepare_arm",
                        lambda external, spec, arm, seed, run_id: SimpleNamespace(arm=arm))
    monkeypatch.setattr(run_pair, "_invoke_subject_agent",
                        lambda setup, spec, seed: SimpleNamespace(arm=setup.arm, error=None))


def test_run_trial_prepares_all_risky_arms(monkeypatch):
    _stub_runner(monkeypatch)
    spec = SimpleNamespace(task_id="T1", harm_label="risky")
    results = run_pair.run_trial(spec, 0, "run_x")
    assert [r.arm for r in results] == list(run_pair.arms_for("risky"))


def test_run_trial_safe_task_omits_oracle(monkeypatch):
    _stub_runner(monkeypatch)
    spec = SimpleNamespace(task_id="B1", harm_label="safe")
    arms = [r.arm for r in run_pair.run_trial(spec, 0, "run_x")]
    assert models.ARM_ORACLE_POSITIVE not in arms and models.ARM_PEBRA in arms


def test_run_trial_honors_explicit_arms(monkeypatch):
    _stub_runner(monkeypatch)
    spec = SimpleNamespace(task_id="T1", harm_label="risky")
    arms = [r.arm for r in run_pair.run_trial(spec, 0, "run_x", arms=(models.ARM_SHAM, models.ARM_PEBRA))]
    assert arms == [models.ARM_SHAM, models.ARM_PEBRA]


def test_run_trial_parallel_is_opt_in(monkeypatch):
    calls = []
    monkeypatch.delenv("E2E_AB_PARALLEL_ARMS", raising=False)
    monkeypatch.setattr(run_pair.rs, "prepare_external_repo", lambda: object())
    monkeypatch.setattr(run_pair, "prepare_arm",
                        lambda external, spec, arm, seed, run_id: SimpleNamespace(arm=arm))

    def invoke(setup, spec, seed):
        calls.append(("start", setup.arm))
        calls.append(("finish", setup.arm))
        return SimpleNamespace(arm=setup.arm, error=None)

    monkeypatch.setattr(run_pair, "_invoke_subject_agent", invoke)
    spec = SimpleNamespace(task_id="T1", harm_label="risky")
    run_pair.run_trial(spec, 0, "run_x", arms=("a", "b"))
    assert calls == [("start", "a"), ("finish", "a"), ("start", "b"), ("finish", "b")]


def test_run_trial_parallel_preserves_arm_order(monkeypatch):
    monkeypatch.setenv("E2E_AB_PARALLEL_ARMS", "1")
    monkeypatch.setenv("E2E_AB_MAX_WORKERS", "2")
    monkeypatch.setattr(run_pair.rs, "prepare_external_repo", lambda: object())
    monkeypatch.setattr(run_pair, "prepare_arm",
                        lambda external, spec, arm, seed, run_id: SimpleNamespace(arm=arm))
    barrier = threading.Barrier(2, timeout=2)
    finished = []

    def invoke(setup, spec, seed):
        barrier.wait()
        if setup.arm == "a":
            time.sleep(0.05)
        finished.append(setup.arm)
        return SimpleNamespace(arm=setup.arm, error=None)

    monkeypatch.setattr(run_pair, "_invoke_subject_agent", invoke)
    spec = SimpleNamespace(task_id="T1", harm_label="risky")
    results = run_pair.run_trial(spec, 0, "run_x", arms=("a", "b"))
    assert finished == ["b", "a"]
    assert [r.arm for r in results] == ["a", "b"]


def test_parallel_worker_count_is_bounded(monkeypatch):
    monkeypatch.delenv("E2E_AB_MAX_WORKERS", raising=False)
    assert run_pair._max_arm_workers(5) == 5
    monkeypatch.setenv("E2E_AB_MAX_WORKERS", "2")
    assert run_pair._max_arm_workers(5) == 2
    monkeypatch.setenv("E2E_AB_MAX_WORKERS", "999")
    assert run_pair._max_arm_workers(5) == 5
    monkeypatch.setenv("E2E_AB_MAX_WORKERS", "not-an-int")
    assert run_pair._max_arm_workers(5) == 5
