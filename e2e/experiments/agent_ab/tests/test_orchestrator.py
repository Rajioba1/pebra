"""Orchestrator glue: plan order, atomic write, crash-survivable resume, and the end-to-end
SubjectResult -> score -> aggregate -> report path — all with fakes, no LLM / clone / dotnet."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import RunOutcome, SubjectResult, TaskSpec
from e2e.experiments.agent_ab.runners import orchestrator

_ORIGINAL_ASSERT_HARNESS_CLEAN = orchestrator._assert_harness_clean

_T1 = TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)
_B1 = TaskSpec("B1", "d", ("a.cs",), "safe", ("a.cs",), "none", False)


@pytest.fixture(autouse=True)
def _stable_harness_identity(monkeypatch):
    commit = orchestrator._git_commit() or "test-harness-head"
    monkeypatch.setattr(orchestrator, "_assert_harness_clean", lambda root=None: commit)


def _outcome(task_id: str, arm: str, seed: int = 0) -> RunOutcome:
    return RunOutcome(
        task_id=task_id, arm=arm, seed=seed, harm_label="risky", harm_materialized=False,
        task_completed=True, over_cautious=False, quality_failure=False, scope_drift=False,
        build_failed=False, test_failed=False, edit_cycle_count=1, advisory_called=False,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_DID_NOT_CALL,
        blinding_leak=False, blinding_terms=(), timed_out=False,
    )


def _rca_meta(*, status="absent", version=None, sha256=None):
    source = "37e5d83c056c8cbf827223d5814a93c5218df1a9" if status == "accepted" else None
    return {
        "status": status,
        "validation_mode": "cargo_revision" if status == "accepted" else None,
        "version": version,
        "sha256": sha256,
        "source_revision": source,
        "required_sha256": None,
        "accepted_version": "0.0.25",
        "required_source_revision": "37e5d83c056c8cbf827223d5814a93c5218df1a9",
    }


def _rca_toolchain_config() -> dict:
    return {
        "toolchain": {
            "rca": {
                "version": "0.0.25",
                "source_revision": "37e5d83c056c8cbf827223d5814a93c5218df1a9",
            }
        }
    }


def _run_meta(*, mode="pilot", seeds_per_arm=1, specs=None) -> dict:
    specs = list(specs or [_T1])
    cfg = {
        mode: {"tasks": [spec.task_id for spec in specs], "seeds_per_arm": seeds_per_arm},
        "bootstrap_seed": 0,
    }
    args = type("Args", (), {"mode": mode})()
    design = orchestrator._experiment_design(
        args, cfg, specs, provider="anthropic", model=None
    )
    return {
        "mode": mode,
        "seeds_per_arm": seeds_per_arm,
        "minimum_pairs_for_efficacy": 3,
        "run_intent": "diagnostic" if seeds_per_arm < 3 else "efficacy",
        "experiment_design": design,
        "experiment_design_sha256": orchestrator._design_sha256(design),
        "rca": _rca_meta(),
    }


def test_plan_is_sorted_and_seeded():
    plan = orchestrator._plan([_T1, _B1], ["B1", "T1"], 2)
    assert plan == [(_B1, 0), (_B1, 1), (_T1, 0), (_T1, 1)]


def test_harness_identity_requires_git_head_and_clean_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "e2e@pebra.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "pebra-e2e"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("clean", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)

    assert _ORIGINAL_ASSERT_HARNESS_CLEAN(repo)

    (repo / "tracked.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(orchestrator.ExperimentRunError, match="uncommitted changes"):
        _ORIGINAL_ASSERT_HARNESS_CLEAN(repo)
    with pytest.raises(orchestrator.ExperimentRunError, match="cannot identify"):
        _ORIGINAL_ASSERT_HARNESS_CLEAN(tmp_path / "not-a-repo")


def test_plan_rejects_missing_tasks():
    try:
        orchestrator._plan([_T1], ["T1", "T9"], 1)
    except ValueError as exc:
        assert "T9" in str(exc)
    else:
        raise AssertionError("missing configured task must fail closed")


def test_orchestrator_corpus_loader_includes_javascript_specimen():
    corpus = orchestrator.load_corpus()
    by_id = {spec.task_id: spec for spec in corpus}
    assert "JS1" in by_id
    assert by_id["JS1"].specimen == "javascript"
    assert by_id["JS1"].harness_id == "node"


def test_live_assess_uses_specimen_oracle_patch(monkeypatch, tmp_path):
    spec = TaskSpec(
        "JS1", "d", ("packages/zod/src/v3/types.ts",), "risky",
        ("packages/zod/src/v3/types.ts",), "build_failure", True,
        build_solution="", language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json", "pnpm-lock.yaml"),
    )
    seen = {}

    def _assess(req_path, *, repo_root, db):
        payload = json.loads(Path(req_path).read_text(encoding="utf-8"))
        seen["patch"] = payload["candidate_actions"][0]["proposed_patch"]
        seen["expected_files"] = payload["candidate_actions"][0]["expected_files"]
        return {"ok": True}

    monkeypatch.setattr(orchestrator.cli_harness, "assess", _assess)

    orchestrator._live_assess_fn(tmp_path, spec)

    assert "packages/zod/src/v3/types.ts" in seen["patch"]
    assert seen["expected_files"] == [
        "packages/zod/src/v3/helpers/util.ts",
        "packages/zod/src/v3/types.ts",
    ]


def test_config_applies_model_override(monkeypatch):
    monkeypatch.setenv("E2E_AB_MODEL", "claude-haiku-4-5-20251001")
    cfg = orchestrator._config()
    assert cfg["subject"]["model"] == "claude-haiku-4-5-20251001"


def test_config_has_one_pair_smoke_mode():
    cfg = orchestrator._config()
    assert cfg["smoke"]["tasks"] == ["T1"]


def test_config_has_javascript_assay_mode():
    cfg = orchestrator._config()
    assert cfg["assay_js"]["tasks"] == ["JS4"]
    assert cfg["assay_js"]["seeds_per_arm"] == 1
    assert "Diagnostic-only" in cfg["assay_js"]["claims"]
    assert cfg["smoke"]["seeds_per_arm"] == 1
    assert cfg["smoke"]["total_runs"] == 2


def test_run_metadata_records_rca_fingerprint_and_pin(monkeypatch):
    cfg = orchestrator._config()
    monkeypatch.setattr(
        orchestrator.rca_probe,
        "fingerprint",
        lambda **kwargs: {
            "status": "accepted", "validation_mode": "cargo_revision",
            "version": "0.0.25", "sha256": "abc",
            "source_revision": kwargs["required_source_revision"], "required_sha256": None,
        },
    )
    args = type("Args", (), {"mode": "assay_js"})()

    metadata = orchestrator._run_metadata(args, cfg)

    assert metadata["rca"]["version"] == "0.0.25"
    assert metadata["rca"]["accepted_version"] == "0.0.25"
    assert metadata["rca"]["sha256"] == "abc"
    assert metadata["rca"]["required_source_revision"] == (
        "37e5d83c056c8cbf827223d5814a93c5218df1a9"
    )
    assert metadata["seeds_per_arm"] == 1
    assert metadata["minimum_pairs_for_efficacy"] == 3
    assert metadata["run_intent"] == "diagnostic"
    assert metadata["experiment_design_sha256"]
    design = metadata["experiment_design"]
    assert design["provider"] == "anthropic"
    assert design["model"] == "claude-haiku-4-5-20251001"
    assert design["mode_config"]["tasks"] == ["JS4"]
    assert design["subject_prompt_template_sha256"]
    assert set(design["protocol_hashes"]) >= {"sham", "pebra"}
    assert metadata["human_approval_policy"] == "always_approve"
    assert design["execution"]["human_approval_policy"] == "always_approve"


def test_run_metadata_records_diagnostic_thinking_override(monkeypatch):
    monkeypatch.setenv("E2E_AB_PROVIDER", "deepseek")
    monkeypatch.setenv("E2E_AB_THINKING", "0")
    args = type("Args", (), {"mode": "assay_js"})()

    metadata = orchestrator._run_metadata(args, orchestrator._config())

    assert metadata["thinking_mode"] == "disabled"
    assert metadata["env"]["E2E_AB_THINKING"] == "0"


def test_experiment_design_hash_changes_with_provider_model_prompt_tasks_and_arms(monkeypatch):
    cfg = orchestrator._config()
    args = type("Args", (), {"mode": "assay_js"})()
    js1 = next(spec for spec in orchestrator.load_corpus() if spec.task_id == "JS1")
    base = orchestrator._experiment_design(
        args, cfg, [js1], provider="deepseek", model="deepseek-v4-flash"
    )
    changed_model = orchestrator._experiment_design(
        args, cfg, [js1], provider="anthropic", model="claude-test"
    )
    monkeypatch.setenv("E2E_AB_PARALLEL_ARMS", "1")
    monkeypatch.setenv("E2E_AB_MAX_WORKERS", "5")
    monkeypatch.setenv("PEBRA_CODEGRAPH_SEMANTIC_DIFF", "1")
    changed_execution = orchestrator._experiment_design(
        args, cfg, [js1], provider="deepseek", model="deepseek-v4-flash"
    )
    monkeypatch.delenv("E2E_AB_PARALLEL_ARMS")
    monkeypatch.delenv("E2E_AB_MAX_WORKERS")
    monkeypatch.delenv("PEBRA_CODEGRAPH_SEMANTIC_DIFF")
    changed_source = orchestrator._experiment_design(
        args,
        cfg,
        [js1],
        provider="deepseek",
        model="deepseek-v4-flash",
        source_head_sha="different-source-head",
    )
    monkeypatch.setattr(orchestrator.run_pair, "_SUBJECT_PROMPT", "changed prompt")
    changed_prompt = orchestrator._experiment_design(
        args, cfg, [js1], provider="deepseek", model="deepseek-v4-flash"
    )
    changed_task = orchestrator._experiment_design(
        args, cfg, [dataclasses.replace(js1, task_id="JSX")],
        provider="deepseek", model="deepseek-v4-flash",
    )

    hashes = {
        orchestrator._design_sha256(design)
        for design in (
            base, changed_model, changed_execution, changed_source, changed_prompt, changed_task,
        )
    }
    assert len(hashes) == 6
    assert base["arm_topology"]["JS1"] == list(orchestrator.run_pair.arms_for("risky"))


def test_resume_rejects_changed_rca_binary_fingerprint(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "outcomes.json").write_text(json.dumps({
        "outcomes": [],
        "run_metadata": {"rca": _rca_meta(status="accepted", version="0.0.25", sha256="old")},
    }), encoding="utf-8")

    with pytest.raises(orchestrator.ExperimentRunError, match="RCA fingerprint changed"):
        orchestrator._assert_resume_rca_compatible(
            run_dir, {"rca": _rca_meta(status="accepted", version="0.0.25", sha256="new")}
        )


def test_resume_accepts_same_rca_binary_fingerprint(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "outcomes.json").write_text(json.dumps({
        "outcomes": [],
        "run_metadata": {"rca": _rca_meta(status="accepted", version="0.0.25", sha256="same")},
    }), encoding="utf-8")

    orchestrator._assert_resume_rca_compatible(
        run_dir, {"rca": _rca_meta(status="accepted", version="0.0.25", sha256="same")}
    )


def test_resume_rejects_changed_rca_acceptance_state_with_same_binary(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "outcomes.json").write_text(json.dumps({
        "outcomes": [],
        "run_metadata": {
            "rca": _rca_meta(status="accepted", version="0.0.25", sha256="same")
        },
    }), encoding="utf-8")

    with pytest.raises(orchestrator.ExperimentRunError, match="RCA fingerprint changed"):
        orchestrator._assert_resume_rca_compatible(
            run_dir,
            {"rca": {**_rca_meta(status="rejected", version="0.0.25", sha256="same")}},
        )


def test_resume_accepts_stable_known_rca_absence(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "outcomes.json").write_text(json.dumps({
        "outcomes": [],
        "run_metadata": {"rca": _rca_meta()},
    }), encoding="utf-8")

    orchestrator._assert_resume_rca_compatible(
        run_dir, {"rca": _rca_meta()}
    )


def test_resume_rejects_changed_seed_design(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "outcomes.json").write_text(json.dumps({
        "outcomes": [],
        "run_metadata": _run_meta(mode="assay_js", seeds_per_arm=1),
    }), encoding="utf-8")

    with pytest.raises(orchestrator.ExperimentRunError, match="run design changed"):
        orchestrator._assert_resume_design_compatible(
            run_dir, _run_meta(mode="assay_js", seeds_per_arm=3)
        )


def test_rca_probe_error_blocks_run_before_resume_or_model_calls():
    with pytest.raises(orchestrator.ExperimentRunError, match="probe failed"):
        orchestrator._assert_rca_probe_usable({"rca": _rca_meta(status="probe_error")})


def test_active_run_rejects_changed_rca_fingerprint(monkeypatch):
    monkeypatch.setattr(
        orchestrator.rca_probe,
        "fingerprint",
        lambda **kwargs: {
            "status": "accepted", "validation_mode": "cargo_revision",
            "version": "0.0.25", "sha256": "changed",
            "source_revision": kwargs["required_source_revision"],
            "required_sha256": None,
        },
    )

    with pytest.raises(orchestrator.ExperimentRunError, match="changed during this run"):
        orchestrator._assert_active_rca_compatible(
            {"rca": _rca_meta(status="accepted", version="0.0.25", sha256="original")},
            _rca_toolchain_config(),
        )


def test_active_run_accepts_unchanged_rca_fingerprint(monkeypatch):
    monkeypatch.setattr(
        orchestrator.rca_probe,
        "fingerprint",
        lambda **kwargs: {
            "status": "accepted", "validation_mode": "cargo_revision",
            "version": "0.0.25", "sha256": "same",
            "source_revision": kwargs["required_source_revision"],
            "required_sha256": None,
        },
    )

    orchestrator._assert_active_rca_compatible(
        {"rca": _rca_meta(status="accepted", version="0.0.25", sha256="same")},
        _rca_toolchain_config(),
    )


def test_active_run_rejects_changed_or_dirty_harness(monkeypatch):
    metadata = {"experiment_design": {"git_commit": "original"}}
    monkeypatch.setattr(orchestrator, "_assert_harness_clean", lambda: "changed")

    with pytest.raises(orchestrator.ExperimentRunError, match="harness changed during this run"):
        orchestrator._assert_active_harness_compatible(metadata)

    def _dirty():
        raise orchestrator.ExperimentRunError("harness Git checkout has uncommitted changes")

    monkeypatch.setattr(orchestrator, "_assert_harness_clean", _dirty)
    with pytest.raises(orchestrator.ExperimentRunError, match="uncommitted changes"):
        orchestrator._assert_active_harness_compatible(metadata)


def test_assay_config_does_not_expose_dead_arm_list():
    cfg = orchestrator._config()
    assert "arms" not in cfg["assay"]


def test_write_outcomes_atomic(tmp_path):
    path = tmp_path / "outcomes.json"
    orchestrator._write_outcomes(path, [_outcome("T1", models.ARM_CONTROL)], "rid")
    assert path.exists() and not path.with_suffix(".tmp").exists()
    payload = json.loads(path.read_text())
    assert payload["run_id"] == "rid" and len(payload["outcomes"]) == 1


def test_completed_pairs_requires_both_arms():
    outcomes = [
        _outcome("T1", models.ARM_CONTROL, 0), _outcome("T1", models.ARM_TREATMENT, 0),  # full
        _outcome("T1", models.ARM_CONTROL, 1),                                            # partial
    ]
    assert orchestrator._completed_pairs(outcomes) == {("T1", 0)}


def test_load_existing_outcomes_roundtrip(tmp_path):
    path = tmp_path / "outcomes.json"
    orchestrator._write_outcomes(path, [_outcome("T1", models.ARM_CONTROL)], "rid")
    loaded = orchestrator._load_existing_outcomes(path)
    assert len(loaded) == 1 and loaded[0].task_id == "T1"
    assert loaded[0].blinding_terms == ()  # list -> tuple on reload


def test_load_existing_outcomes_roundtrip_served_models(tmp_path):
    path = tmp_path / "outcomes.json"
    outcome = dataclasses.replace(_outcome("T1", models.ARM_TREATMENT), served_models=("m1", "m2"))
    orchestrator._write_outcomes(path, [outcome], "rid")
    loaded = orchestrator._load_existing_outcomes(path)
    assert loaded[0].served_models == ("m1", "m2")


def _wire(monkeypatch, tmp_path, corpus, run_pair_fn):
    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: corpus)
    monkeypatch.setattr(orchestrator, "_config",
                        lambda: {"pilot": {"tasks": ["T1"], "seeds_per_arm": 1}, "bootstrap_seed": 0})
    monkeypatch.setattr(orchestrator.run_pair, "run_pair", run_pair_fn)


def test_main_end_to_end_writes_report(monkeypatch, tmp_path):
    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    rc = orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    assert rc == 0
    assert (tmp_path / "t1" / "outcomes.json").exists()
    assert (tmp_path / "t1" / "reports" / "ab_t1.md").exists()
    assert (tmp_path / "t1" / "reports" / "ab_t1.json").exists()


def test_main_writes_finished_run_status(monkeypatch, tmp_path):
    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    monkeypatch.delenv("E2E_AB_PARALLEL_ARMS", raising=False)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    status = json.loads((tmp_path / "t1" / "run_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "finished"
    assert status["mode"] == "pilot"
    assert status["run_id"] == "t1"
    assert status["updated_at"]  # an ISO timestamp is stamped
    assert status["run_metadata"]["git_commit"]
    assert status["run_metadata"]["provider"] == "anthropic"
    assert status["run_metadata"]["parallel_arms"] is False
    assert status["run_metadata"]["protocol_file"] == ".agent-instructions/edit_protocol.md"
    assert set(status["run_metadata"]["protocol_hashes"]) >= {"sham", "pebra"}


def test_main_run_status_records_parallel_arms_when_enabled(monkeypatch, tmp_path):
    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    monkeypatch.setenv("E2E_AB_PARALLEL_ARMS", "1")
    monkeypatch.setenv("E2E_AB_MAX_WORKERS", "5")

    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])

    status = json.loads((tmp_path / "t1" / "run_status.json").read_text(encoding="utf-8"))
    assert status["run_metadata"]["parallel_arms"] is True
    assert status["run_metadata"]["max_workers_env"] == "5"


def test_main_resumes_and_skips_completed_pair(monkeypatch, tmp_path):
    # Pre-seed a completed pair for (T1, 0); run_pair must NOT be called again.
    out_path = tmp_path / "t1" / "outcomes.json"
    orchestrator._write_outcomes(
        out_path,
        [_outcome("T1", models.ARM_CONTROL, 0), _outcome("T1", models.ARM_TREATMENT, 0)],
        "t1",
        run_metadata=_run_meta(),
    )

    def _must_not_run(spec, seed, run_id):
        raise AssertionError("run_pair called for an already-completed pair")

    _wire(monkeypatch, tmp_path, [_T1], _must_not_run)
    monkeypatch.setattr(
        orchestrator,
        "_config",
        lambda: {
            "pilot": {"tasks": ["T1"], "seeds_per_arm": 1},
            "bootstrap_seed": 0,
            **_rca_toolchain_config(),
        },
    )
    monkeypatch.setattr(
        orchestrator.rca_probe,
        "fingerprint",
        lambda **kwargs: {
            "status": "absent", "validation_mode": None, "version": None,
            "sha256": None, "source_revision": None, "required_sha256": None,
        },
    )
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    rc = orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    assert rc == 0
    assert (tmp_path / "t1" / "reports" / "ab_t1.md").exists()  # still renders from loaded outcomes


def test_main_fails_fast_on_errored_run(monkeypatch, tmp_path):
    # A live-client error captured into SubjectResult.error must abort, not be silently scored.
    def _erroring_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed,
                              error="AuthenticationError: invalid x-api-key"),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _erroring_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    try:
        orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    except orchestrator.ExperimentRunError as exc:
        assert "invalid x-api-key" in str(exc)
    else:
        raise AssertionError("errored run must fail-fast, not be silently scored")
    assert not (tmp_path / "t1" / "outcomes.json").exists()  # aborted pair not written
    status = json.loads((tmp_path / "t1" / "run_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "failed"
    assert "invalid x-api-key" in status["error"]


def test_scoring_mode_build_break_when_no_evaluator_tests():
    # No specimen evaluator_tests/<id>/ dirs exist for these -> build_break_scope.
    assert orchestrator._scoring_mode([_T1, _B1]) == "build_break_scope"


def test_scoring_mode_requires_real_evaluator_project(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_EVAL_DIR", tmp_path / "eval")
    (tmp_path / "eval" / "T1").mkdir(parents=True)
    assert orchestrator._scoring_mode([_T1, _B1]) == "build_break_scope"
    (tmp_path / "eval" / "T1" / "Evaluator.csproj").write_text("<Project />")
    assert orchestrator._scoring_mode([_T1, _B1]) == "mixed_build_test_scope"
    (tmp_path / "eval" / "B1").mkdir()
    (tmp_path / "eval" / "B1" / "Evaluator.csproj").write_text("<Project />")
    assert orchestrator._scoring_mode([_T1, _B1]) == "build_test_scope"


def test_scoring_mode_treats_existing_repo_test_filter_as_test_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "_EVAL_DIR", tmp_path / "eval")
    gamma = TaskSpec(
        "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",), "test_failure", False,
        evaluator_test_project="tests/Tests.csproj", evaluator_test_filter="FullyQualifiedName~GammaTests",
    )
    assert orchestrator._scoring_mode([gamma]) == "build_test_scope"


def test_main_reports_scoring_mode_for_planned_tasks_only(monkeypatch, tmp_path):
    seen = {}

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    def _capture_report(*_args, **kwargs):
        seen["scoring_mode"] = kwargs["scoring_mode"]

    _wire(monkeypatch, tmp_path, [_T1, _B1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    monkeypatch.setattr(orchestrator, "_scoring_mode", lambda specs: ",".join(s.task_id for s in specs))
    monkeypatch.setattr(orchestrator.render_report, "write_report", _capture_report)

    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])

    assert seen["scoring_mode"] == "T1"


def test_main_runs_preflights_for_planned_tasks_only(monkeypatch, tmp_path):
    seen = {}

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1, _B1], _fake_pair)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight",
                        lambda specs, *_args, **_kwargs: seen.setdefault("oracle", [s.task_id for s in specs]))
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight",
                        lambda specs, *_args, **_kwargs: seen.setdefault("graph", [s.task_id for s in specs]))

    orchestrator.main(["--run-id", "t1"])

    assert seen == {"oracle": ["T1"], "graph": ["T1"]}


def test_main_runs_revise_safer_calibration_for_assay(monkeypatch, tmp_path):
    gamma = TaskSpec(
        "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",), "test_failure", False,
        evaluator_test_project="tests/Tests.csproj", evaluator_test_filter="FullyQualifiedName~GammaTests",
        build_solution="MathNet.Numerics.sln",
    )
    calls: list[str] = []
    seen = {}

    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [gamma])
    monkeypatch.setattr(orchestrator, "_config",
                        lambda: {"assay": {"tasks": ["MNGAMMA"], "seeds_per_arm": 1},
                                 "bootstrap_seed": 0})
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight",
                        lambda *a, **k: calls.append("oracle"))
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight",
                        lambda *a, **k: calls.append("graph"))
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration",
                        lambda *a, **k: calls.append("revise"))
    monkeypatch.setattr(orchestrator.run_pair, "run_trial",
                        lambda spec, seed, run_id: tuple(
                            SubjectResult(task_id=spec.task_id, arm=arm, seed=seed)
                            for arm in orchestrator.run_pair.arms_for(spec.harm_label)
                        ))
    monkeypatch.setattr(orchestrator.render_report, "write_assay_report",
                        lambda *a, **k: seen.setdefault("preflight_status", k["preflight_status"]))

    orchestrator.main(["--run-id", "assay1", "--mode", "assay"])

    assert calls == ["oracle", "graph", "revise"]
    assert seen["preflight_status"]["revise_safer"] == "passed"


def test_preflight_only_js_assay_skips_paid_run_gate_and_subject(monkeypatch, tmp_path):
    js1 = TaskSpec(
        "JS1", "d", ("packages/zod/src/v3/types.ts",), "risky",
        ("packages/zod/src/v3/types.ts",), "build_failure", True,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    js2 = TaskSpec(
        "JS2", "d", ("packages/zod/src/v3/types.ts",), "safe",
        ("packages/zod/src/v3/types.ts",), "none", False,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    calls: list[str] = []

    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate",
                        lambda: (_ for _ in ()).throw(AssertionError("paid gate must be skipped")))
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [js1, js2])
    monkeypatch.setattr(orchestrator, "_config",
                        lambda: {"assay_js": {"tasks": ["JS1", "JS2"], "seeds_per_arm": 1},
                                 "bootstrap_seed": 0})
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight",
                        lambda *a, **k: calls.append("identity"))
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight",
                        lambda *a, **k: calls.append("oracle"))
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight",
                        lambda *a, **k: calls.append("graph"))
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration",
                        lambda *a, **k: calls.append("revise"))
    monkeypatch.setattr(orchestrator.run_pair, "run_trial",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("subject must not run")))

    assert orchestrator.main(["--run-id", "js-pf", "--mode", "assay_js", "--preflight-only"]) == 0
    assert calls == ["identity", "oracle", "graph", "revise"]


def test_preflight_only_rejects_preflight_skip_flags(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, [_T1], lambda spec, seed, run_id: (_outcome("T1", "control"),
                                                                   _outcome("T1", "treatment")))
    with pytest.raises(orchestrator.ExperimentRunError, match="preflight-only"):
        orchestrator.main([
            "--run-id", "pf",
            "--preflight-only",
            "--skip-oracle-preflight",
            "--skip-graph-preflight",
        ])


def test_assay_js_stops_after_sham_stage_when_no_headroom(monkeypatch, tmp_path):
    js1 = TaskSpec(
        "JS1", "d", ("src/a.ts",), "risky", ("src/a.ts",), "build_failure", True,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    calls: list[tuple[str, ...] | None] = []

    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [js1])
    monkeypatch.setattr(
        orchestrator,
        "_config",
        lambda: {
            "assay_js": {"tasks": ["JS1"], "seeds_per_arm": 1},
            "bootstrap_seed": 0,
            **_rca_toolchain_config(),
        },
    )
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration", lambda *a, **k: None)

    def _trial(spec, seed, run_id, *, arms=None):
        calls.append(arms)
        if arms != (models.ARM_SHAM,):
            raise AssertionError("non-sham arms must not run without measured headroom")
        return (SubjectResult(
            task_id=spec.task_id,
            arm=models.ARM_SHAM,
            seed=seed,
            tool_calls=(models.ToolCallRecord(sequence=1, name="write_file"),),
            modified_files=("src/a.ts",),
            build_ran=True,
            build_passed=True,
        ),)

    monkeypatch.setattr(orchestrator.run_pair, "run_trial", _trial)

    with pytest.raises(orchestrator.ExperimentRunError, match="sham admission"):
        orchestrator.main(["--run-id", "js-no-headroom", "--mode", "assay_js"])

    assert calls == [(models.ARM_SHAM,)]
    status = json.loads((tmp_path / "js-no-headroom" / "run_status.json").read_text())
    assert status["phase"] == "no_headroom"
    assert status["failure_kind"] == "sham_no_headroom"
    assert "0/1 scorable" in status["error"]


def test_assay_js_distinguishes_all_no_attempt_sham_from_no_headroom(monkeypatch, tmp_path):
    js1 = TaskSpec(
        "JS1", "d", ("src/a.ts",), "risky", ("src/a.ts",), "build_failure", True,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [js1])
    monkeypatch.setattr(
        orchestrator,
        "_config",
        lambda: {"assay_js": {"tasks": ["JS1"], "seeds_per_arm": 1}, "bootstrap_seed": 0},
    )
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration", lambda *a, **k: None)
    monkeypatch.setattr(
        orchestrator.run_pair,
        "run_trial",
        lambda spec, seed, run_id, *, arms=None: (
            SubjectResult(
                task_id=spec.task_id,
                arm=models.ARM_SHAM,
                seed=seed,
                timed_out=True,
                limit_reason="wall_clock",
            ),
        ),
    )

    with pytest.raises(orchestrator.ExperimentRunError, match="insufficient data"):
        orchestrator.main(["--run-id", "js-no-data", "--mode", "assay_js"])

    status = json.loads((tmp_path / "js-no-data" / "run_status.json").read_text())
    assert status["phase"] == "insufficient_data"
    assert status["failure_kind"] == "sham_no_scorable_runs"
    assert "0 scorable" in status["error"]


def test_assay_js_resume_retries_unscorable_sham(monkeypatch, tmp_path):
    js1 = TaskSpec(
        "JS1", "d", ("src/a.ts",), "risky", ("src/a.ts",), "build_failure", True,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    run_dir = tmp_path / "retry-sham"
    orchestrator._write_outcomes(
        run_dir / "outcomes.json",
        [dataclasses.replace(_outcome("JS1", models.ARM_SHAM), no_attempt=True, timed_out=True)],
        "retry-sham",
        run_metadata=_run_meta(mode="assay_js", specs=[js1]),
    )
    calls: list[tuple[str, ...] | None] = []
    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [js1])
    monkeypatch.setattr(
        orchestrator,
        "_config",
        lambda: {
            "assay_js": {"tasks": ["JS1"], "seeds_per_arm": 1},
            "bootstrap_seed": 0,
            **_rca_toolchain_config(),
        },
    )
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration", lambda *a, **k: None)
    monkeypatch.setattr(
        orchestrator.rca_probe,
        "fingerprint",
        lambda **kwargs: {
            "status": "absent", "validation_mode": None, "version": None,
            "sha256": None, "source_revision": None, "required_sha256": None,
        },
    )

    def _trial(spec, seed, run_id, *, arms=None):
        calls.append(arms)
        if arms == (models.ARM_SHAM,):
            return (SubjectResult(
                task_id=spec.task_id, arm=models.ARM_SHAM, seed=seed,
                tool_calls=(models.ToolCallRecord(sequence=1, name="write_file"),),
                modified_files=("src/a.ts",), build_ran=True, build_passed=False,
            ),)
        return tuple(SubjectResult(task_id=spec.task_id, arm=arm, seed=seed) for arm in arms)

    monkeypatch.setattr(orchestrator.run_pair, "run_trial", _trial)
    monkeypatch.setattr(
        orchestrator.oracle,
        "score_run",
        lambda subject, spec: dataclasses.replace(
            _outcome(spec.task_id, subject.arm, subject.seed),
            harm_materialized=subject.arm == models.ARM_SHAM,
        ),
    )

    assert orchestrator.main(["--run-id", "retry-sham", "--mode", "assay_js"]) == 0
    assert calls[0] == (models.ARM_SHAM,)


def test_terminal_run_status_retries_transient_windows_replace_error(monkeypatch, tmp_path):
    calls = 0

    def _flaky_write(path, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("temporarily held by dashboard reader")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(orchestrator.run_artifacts, "atomic_write_json", _flaky_write)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda seconds: None)

    orchestrator._write_run_status(tmp_path, "assay_js", "no_headroom", error="no headroom")

    assert calls == 2
    status = json.loads((tmp_path / "run_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "no_headroom"


def test_assay_js_reuses_passing_sham_stage_without_running_sham_twice(monkeypatch, tmp_path):
    js1 = TaskSpec(
        "JS1", "d", ("src/a.ts",), "risky", ("src/a.ts",), "build_failure", True,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    calls: list[tuple[str, ...] | None] = []

    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [js1])
    monkeypatch.setattr(
        orchestrator,
        "_config",
        lambda: {"assay_js": {"tasks": ["JS1"], "seeds_per_arm": 1}, "bootstrap_seed": 0},
    )
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration", lambda *a, **k: None)

    def _trial(spec, seed, run_id, *, arms=None):
        calls.append(arms)
        return tuple(SubjectResult(task_id=spec.task_id, arm=arm, seed=seed) for arm in arms)

    def _score(subject, spec):
        return dataclasses.replace(
            _outcome(spec.task_id, subject.arm, subject.seed),
            harm_materialized=subject.arm == models.ARM_SHAM,
        )

    monkeypatch.setattr(orchestrator.run_pair, "run_trial", _trial)
    monkeypatch.setattr(orchestrator.oracle, "score_run", _score)

    assert orchestrator.main(["--run-id", "js-headroom", "--mode", "assay_js"]) == 0
    assert calls[0] == (models.ARM_SHAM,)
    assert models.ARM_SHAM not in calls[1]
    assert set(calls[1]) == set(orchestrator.run_pair.arms_for("risky")) - {models.ARM_SHAM}


def test_main_runs_repo_identity_preflight_before_external_clone(monkeypatch, tmp_path):
    calls: list[str] = []

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: source)

    def _identity(specs, source_root):
        calls.append("identity")
        assert source_root == source
        assert [s.task_id for s in specs] == ["T1"]

    def _prepare(source_root):
        calls.append("prepare")
        assert source_root == source
        return object()

    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", _identity)
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", _prepare)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)

    orchestrator.main(["--run-id", "t1"])

    assert calls[:2] == ["identity", "prepare"]


def test_skip_preflight_requires_debug_env(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, [_T1], lambda spec, seed, run_id: (_outcome("T1", "control"),
                                                                   _outcome("T1", "treatment")))
    monkeypatch.delenv("E2E_AB_ALLOW_UNVERIFIED", raising=False)
    with pytest.raises(orchestrator.ExperimentRunError, match="E2E_AB_ALLOW_UNVERIFIED"):
        orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight"])


def test_skipped_preflight_status_is_reported(monkeypatch, tmp_path):
    seen = {}

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    def _capture_report(*_args, **kwargs):
        seen["preflight_status"] = kwargs["preflight_status"]

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.render_report, "write_report", _capture_report)

    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight"])

    assert seen["preflight_status"]["oracle"] == "skipped"
    assert seen["preflight_status"]["graph"] == "passed"
