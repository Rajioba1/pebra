"""Pre-flight gates: pure assertion helpers for oracle-outcome and graph-freshness (mock injection)."""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import preflight

_TRAP = TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)
_SAFE = TaskSpec("B1", "d", ("a.cs",), "safe", ("a.cs",), "none", False)
_TEST_TRAP = TaskSpec(
    "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",), "test_failure", False,
    evaluator_test_project="tests/Tests.csproj", evaluator_test_filter="FullyQualifiedName~GammaTests",
)
_FULL_TIER_TRAP = TaskSpec(
    "TSSEM", "d", ("src/a.ts",), "risky", ("src/a.ts",), "test_failure", False,
    required_language_tier="full",
)
_JS_TRAP = TaskSpec(
    "JS1", "d", ("src/a.ts",), "risky", ("src/a.ts",), "build_failure", True,
    language="typescript", harness_id="node", specimen="javascript",
    build_profile="zshy", build_selector="zod:tsconfig.build.json",
)
_MEASURED_BENEFIT_TRAP = TaskSpec(
    "JSBEN", "d", ("src/a.ts",), "risky", ("src/a.ts",), "build_failure", True,
    language="typescript", harness_id="node", specimen="javascript",
    required_language_tier="full", requires_measured_benefit=True,
)


def _build(ran=True, passed=True, err=""):
    return SimpleNamespace(ran=ran, passed=passed, error_summary=err)


# ---- oracle-outcome assertions ----

def test_trap_that_fails_is_ok():
    assert preflight._oracle_failure(_TRAP, _build(passed=False, err="CS0535")) is None


def test_safe_that_passes_is_ok():
    assert preflight._oracle_failure(_SAFE, _build(passed=True)) is None


def test_correct_fix_patch_outside_expected_scope_is_flagged(tmp_path):
    patch = tmp_path / "T1_correct.patch"
    patch.write_text(
        "diff --git a/a.cs b/a.cs\n"
        "index 1111111..2222222 100644\n"
        "--- a/a.cs\n"
        "+++ b/a.cs\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/extra.cs b/extra.cs\n"
        "index 1111111..2222222 100644\n"
        "--- a/extra.cs\n"
        "+++ b/extra.cs\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    msg = preflight._correct_fix_scope_failure(_TRAP, patch)
    assert msg and "extra.cs" in msg


def test_correct_fix_patch_inside_expected_scope_is_ok(tmp_path):
    patch = tmp_path / "T1_correct.patch"
    patch.write_text(
        "diff --git a/a.cs b/a.cs\n"
        "index 1111111..2222222 100644\n"
        "--- a/a.cs\n"
        "+++ b/a.cs\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    assert preflight._correct_fix_scope_failure(_TRAP, patch) is None


def test_trap_that_builds_is_flagged():
    msg = preflight._oracle_failure(_TRAP, _build(passed=True))
    assert msg and "MUST fail" in msg


def test_safe_that_fails_is_flagged():
    msg = preflight._oracle_failure(_SAFE, _build(passed=False, err="boom"))
    assert msg and "should pass" in msg


def test_build_did_not_run_is_flagged():
    assert "did not run" in preflight._oracle_failure(_TRAP, _build(ran=False))


# ---- graph-freshness assertions ----

def _payload(freshness, resolution):
    return {"scores": {"symbol_scope_evidence": {"symbol_fanin": {
        "graph_freshness": freshness, "resolution_method": resolution,
        "caller_count": 3, "modify_impact_count": 4}}, "expected_loss": 0.2}}


def test_fresh_resolved_graph_is_ok():
    assert preflight._graph_backed_failure(_TRAP, _payload("fresh", "location")) is None


def test_name_fallback_is_not_strong_enough_for_ab_preflight():
    msg = preflight._graph_backed_failure(_TRAP, _payload("fresh", "name_fallback"))
    assert msg and "location" in msg


def test_fresh_resolved_but_zero_impact_graph_is_flagged():
    payload = {"scores": {"symbol_scope_evidence": {"symbol_fanin": {
        "graph_freshness": "fresh", "resolution_method": "location",
        "caller_count": 0, "modify_impact_count": 0, "modify_transitive_impact_count": 0}}},
        "expected_loss": 0.0}
    msg = preflight._graph_backed_failure(_TRAP, payload)
    assert msg and "material" in msg


def test_stale_graph_is_flagged():
    msg = preflight._graph_backed_failure(_TRAP, _payload("stale", "location"))
    assert msg and "not fresh" in msg


def test_unresolved_target_is_flagged():
    msg = preflight._graph_backed_failure(_TRAP, _payload("fresh", "unresolved"))
    assert msg and "did not resolve" in msg


def test_missing_evidence_is_flagged():
    assert preflight._graph_backed_failure(_TRAP, {}) is not None


def test_required_language_tier_fails_when_payload_is_lower():
    payload = _payload("fresh", "location")
    payload["graph_provenance"] = {
        "language_capability": {"language": "csharp", "tier": "partial"}
    }

    msg = preflight._graph_backed_failure(_FULL_TIER_TRAP, payload)

    assert msg and "requires language tier full" in msg


def test_required_language_tier_fails_when_payload_omits_capability():
    payload = _payload("fresh", "location")

    msg = preflight._graph_backed_failure(_FULL_TIER_TRAP, payload)

    assert msg and "requires language tier full" in msg


def test_required_language_tier_passes_when_payload_meets_requirement():
    payload = _payload("fresh", "location")
    payload["graph_provenance"] = {
        "language_capability": {"language": "typescript", "tier": "full"}
    }

    assert preflight._graph_backed_failure(_FULL_TIER_TRAP, payload) is None


def test_required_measured_benefit_rejects_projected_assessment():
    payload = _payload("fresh", "location")
    payload["graph_provenance"] = {
        "language_capability": {"language": "typescript", "tier": "full"}
    }
    payload["scores"]["benefit_breakdown"] = {"source_type": "projected"}

    msg = preflight._graph_backed_failure(_MEASURED_BENEFIT_TRAP, payload)

    assert msg and "measured benefit" in msg


def test_required_measured_benefit_accepts_rca_measurement():
    payload = _payload("fresh", "location")
    payload["graph_provenance"] = {
        "language_capability": {"language": "typescript", "tier": "full"}
    }
    payload["scores"]["benefit_breakdown"] = {"source_type": "measured"}

    assert preflight._graph_backed_failure(_MEASURED_BENEFIT_TRAP, payload) is None


def test_benefit_calibration_requires_candidate_specific_signal():
    bad = {"scores": {"benefit": 0.5, "benefit_breakdown": {
        "source_type": "measured", "maintainability_gain": 0.0,
    }}}
    fixed = {"scores": {"benefit": 0.5, "benefit_breakdown": {
        "source_type": "measured", "maintainability_gain": 0.0,
    }}}

    msg = preflight._benefit_discrimination_failure(_MEASURED_BENEFIT_TRAP, bad, fixed)

    assert msg and "did not vary" in msg


def test_benefit_calibration_accepts_finite_candidate_specific_signal():
    bad = {"scores": {"benefit": 0.5, "benefit_breakdown": {
        "source_type": "measured", "maintainability_gain": 0.0,
    }}}
    fixed = {"scores": {"benefit": 0.25, "benefit_breakdown": {
        "source_type": "measured", "maintainability_gain": -0.25,
    }}}

    assert preflight._benefit_discrimination_failure(
        _MEASURED_BENEFIT_TRAP, bad, fixed
    ) is None


# ---- accumulate-ALL-failures (never first-fail) ----

import pytest  # noqa: E402


def test_oracle_preflight_reports_all_missing_patches(tmp_path):
    corpus = [_TRAP, _SAFE]
    empty_patch_dir = tmp_path / "no_patches"
    empty_patch_dir.mkdir()
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_oracle_preflight(corpus, None, out_dir=tmp_path,
                                       build_fn=lambda p: _build(), patch_dir=empty_patch_dir)
    msg = str(ei.value)
    assert "T1" in msg and "B1" in msg  # BOTH reported, not first-fail


def test_oracle_preflight_requires_correct_fix_patch_for_risky_tasks(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "T1.patch").write_text("diff --git a/a.cs b/a.cs\n", encoding="utf-8")
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)
    monkeypatch.setattr(preflight, "_apply_patch", lambda patch_file, repo_path: None)

    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_oracle_preflight([_TRAP], None, out_dir=tmp_path,
                                       build_fn=lambda p: _build(passed=False),
                                       patch_dir=patch_dir, correct_patch_dir=correct_dir)

    assert "missing correct-fix patch" in str(ei.value)


def test_oracle_preflight_applies_correct_fix_patch_for_risky_tasks(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "T1.patch").write_text(
        "diff --git a/a.cs b/a.cs\n--- a/a.cs\n+++ b/a.cs\n@@ -1 +1 @@\n-old\n+bad\n",
        encoding="utf-8",
    )
    (correct_dir / "T1.patch").write_text(
        "diff --git a/a.cs b/a.cs\n--- a/a.cs\n+++ b/a.cs\n@@ -1 +1 @@\n-old\n+good\n",
        encoding="utf-8",
    )
    applied: list[str] = []

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        return dest

    def _apply(patch_file, _repo_path):
        applied.append(patch_file.parent.name)

    def _build(repo_path):
        return _build_result(passed="T1_correct" in repo_path.as_posix())

    def _build_result(passed):
        return SimpleNamespace(ran=True, passed=passed, error_summary="")

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(preflight, "_apply_patch", _apply)

    preflight.run_oracle_preflight([_TRAP], None, out_dir=tmp_path, build_fn=_build,
                                   patch_dir=patch_dir, correct_patch_dir=correct_dir)

    assert applied == ["patches", "correct"]


def test_oracle_preflight_validates_test_failure_and_correct_fix_test_pass(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "MNGAMMA.patch").write_text(
        "diff --git a/src/Gamma.cs b/src/Gamma.cs\n--- a/src/Gamma.cs\n+++ b/src/Gamma.cs\n"
        "@@ -1 +1 @@\n-old\n+bad\n",
        encoding="utf-8",
    )
    (correct_dir / "MNGAMMA.patch").write_text(
        "diff --git a/src/Gamma.cs b/src/Gamma.cs\n--- a/src/Gamma.cs\n+++ b/src/Gamma.cs\n"
        "@@ -1 +1 @@\n-old\n+good\n",
        encoding="utf-8",
    )

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        (dest / "tests").mkdir()
        (dest / "tests" / "Tests.csproj").write_text("<Project />")
        return dest

    calls: list[tuple[str, str]] = []

    def _test(repo_path, *, project=None, test_filter=None):
        assert project.is_absolute()
        calls.append((project.relative_to(repo_path).as_posix(), test_filter))
        return SimpleNamespace(ran=True, passed="correct" in repo_path.as_posix(), error_summary="")

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(preflight, "_apply_patch", lambda patch_file, repo_path: None)

    preflight.run_oracle_preflight([_TEST_TRAP], None, out_dir=tmp_path,
                                   build_fn=lambda p: _build(passed=True), test_fn=_test,
                                   patch_dir=patch_dir, correct_patch_dir=correct_dir)

    assert calls == [
        ("tests/Tests.csproj", "FullyQualifiedName~GammaTests"),
        ("tests/Tests.csproj", "FullyQualifiedName~GammaTests"),
    ]


def test_oracle_preflight_flags_test_failure_oracle_that_passes(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "MNGAMMA.patch").write_text("diff --git a/src/Gamma.cs b/src/Gamma.cs\n")
    (correct_dir / "MNGAMMA.patch").write_text("diff --git a/src/Gamma.cs b/src/Gamma.cs\n")

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        (dest / "tests").mkdir()
        (dest / "tests" / "Tests.csproj").write_text("<Project />")
        return dest

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(preflight, "_apply_patch", lambda patch_file, repo_path: None)

    with pytest.raises(preflight.PreflightError, match="test MUST fail"):
        preflight.run_oracle_preflight([_TEST_TRAP], None, out_dir=tmp_path,
                                       build_fn=lambda p: _build(passed=True),
                                       test_fn=lambda *a, **k: SimpleNamespace(
                                           ran=True, passed=True, error_summary=""),
                                       patch_dir=patch_dir, correct_patch_dir=correct_dir)


def test_oracle_preflight_flags_filtered_test_that_selects_zero_tests(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "MNGAMMA.patch").write_text("diff --git a/src/Gamma.cs b/src/Gamma.cs\n")
    (correct_dir / "MNGAMMA.patch").write_text("diff --git a/src/Gamma.cs b/src/Gamma.cs\n")

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        (dest / "tests").mkdir()
        (dest / "tests" / "Tests.csproj").write_text("<Project />")
        return dest

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(preflight, "_apply_patch", lambda patch_file, repo_path: None)

    with pytest.raises(preflight.PreflightError, match="selected zero tests"):
        preflight.run_oracle_preflight(
            [_TEST_TRAP],
            None,
            out_dir=tmp_path,
            build_fn=lambda p: _build(passed=True),
            test_fn=lambda *a, **k: SimpleNamespace(
                ran=True, passed=False, error_summary="", tests_selected=0),
            patch_dir=patch_dir,
            correct_patch_dir=correct_dir,
        )


def test_repo_identity_preflight_passes_for_planned_specimen(tmp_path):
    (tmp_path / "MathNet.Numerics.sln").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Gamma.cs").write_text("", encoding="utf-8")

    preflight.run_repo_identity_preflight([
        TaskSpec(
            "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",),
            "test_failure", False, build_solution="MathNet.Numerics.sln",
            repo_identity_files=("MathNet.Numerics.sln",),
        )
    ], tmp_path)


def test_repo_identity_preflight_uses_explicit_repo_identity_files_for_node_specimen(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "packages" / "zod" / "src" / "v3").mkdir(parents=True)
    (tmp_path / "packages" / "zod" / "src" / "v3" / "types.ts").write_text("", encoding="utf-8")

    preflight.run_repo_identity_preflight([
        TaskSpec(
            "JS1", "d", ("packages/zod/src/v3/types.ts",), "risky",
            ("packages/zod/src/v3/types.ts",), "build_failure", True,
            build_solution="", language="typescript", harness_id="node",
            specimen="javascript", repo_identity_files=("package.json", "pnpm-lock.yaml"),
        )
    ], tmp_path)


def test_repo_identity_preflight_fails_wrong_repo_with_task_and_env_var(tmp_path):
    (tmp_path / "TemplateBlueprint.sln").write_text("", encoding="utf-8")
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_repo_identity_preflight([
            TaskSpec(
                "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",),
                "test_failure", False, build_solution="MathNet.Numerics.sln",
                repo_identity_files=("MathNet.Numerics.sln",),
            )
        ], tmp_path)

    msg = str(ei.value)
    assert "MNGAMMA" in msg
    assert "E2E_TEMPLATE_BLUEPRINT_REPO" in msg
    assert "MathNet.Numerics.sln" in msg


def test_repo_identity_preflight_fails_mixed_specimen_plan(tmp_path):
    with pytest.raises(preflight.PreflightError, match="spans multiple repositories"):
        preflight.run_repo_identity_preflight([
            TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True),
            TaskSpec(
                "JS1", "d", ("a.ts",), "risky", ("a.ts",), "build_failure", True,
                build_solution="", language="typescript", harness_id="node", specimen="javascript",
                repo_identity_files=("package.json", "pnpm-lock.yaml"),
            ),
        ], tmp_path)


def test_default_patch_dirs_follow_task_specimen():
    js = TaskSpec(
        "JS1", "d", ("a.ts",), "risky", ("a.ts",), "build_failure", True,
        build_solution="", language="typescript", harness_id="node", specimen="javascript",
    )

    assert preflight._oracle_patch_dir(js).as_posix().endswith(
        "specimens/javascript/corpus/oracle_patches"
    )
    assert preflight._correct_patch_dir(js).as_posix().endswith(
        "specimens/javascript/corpus/correct_fix_patches"
    )


def test_oracle_preflight_accumulates_apply_failures(tmp_path, monkeypatch):
    corpus = [_TRAP, _SAFE]
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "T1.patch").write_text("bad")
    (patch_dir / "B1.patch").write_text("bad")
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)

    def _raise_apply(_pf, _rp):
        raise preflight.PreflightError("git apply failed")

    monkeypatch.setattr(preflight, "_apply_patch", _raise_apply)
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_oracle_preflight(corpus, None, out_dir=tmp_path,
                                       build_fn=lambda p: _build(), patch_dir=patch_dir)
    msg = str(ei.value)
    assert "T1" in msg and "B1" in msg  # apply failures accumulated, not short-circuited


def test_oracle_preflight_replaces_stale_preflight_clone(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "B1.patch").write_text("diff --git a/a.cs b/a.cs\n", encoding="utf-8")
    stale_repo = tmp_path / "preflight" / "B1" / "repo"
    stale_repo.mkdir(parents=True)
    stale_file = stale_repo / "stale.txt"
    stale_file.write_text("stale")
    os.chmod(stale_file, stat.S_IREAD)

    def _clone(_external, dest):
        assert not dest.exists()
        dest.mkdir(parents=True)
        return dest

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(preflight, "_apply_patch", lambda patch_file, repo_path: None)

    preflight.run_oracle_preflight([_SAFE], None, out_dir=tmp_path, build_fn=lambda p: _build(),
                                   patch_dir=patch_dir, correct_patch_dir=correct_dir)

    assert not (stale_repo / "stale.txt").exists()


def test_graph_preflight_accumulates_infra_errors(tmp_path, monkeypatch):
    trap2 = TaskSpec("T2", "d", ("b.cs",), "risky", ("b.cs",), "build_failure", True)

    def _boom(ext, dest):
        raise RuntimeError("clone exploded")

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _boom)
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_graph_preflight([_TRAP, trap2], None, out_dir=tmp_path,
                                      assess_fn=lambda rp, sp: {}, setup_graph_fn=None,
                                      node_count_fn=lambda p: {"csharp_callable": 999})
    msg = str(ei.value)
    assert "T1" in msg and "T2" in msg and "infrastructure error" in msg


# ---- independent graph-validity (node-count) check ----

def _fresh_payload(rp, sp):
    return _payload("fresh", "location")


def test_graph_preflight_fails_on_low_node_count(tmp_path, monkeypatch):
    # a 'fresh' index that parsed almost no C# must be flagged even though freshness/resolution pass
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)
    with pytest.raises(preflight.PreflightError) as ei:
        preflight.run_graph_preflight([_TRAP], None, out_dir=tmp_path,
                                      assess_fn=_fresh_payload, setup_graph_fn=None,
                                      node_count_fn=lambda p: {"csharp_callable": 3})
    assert "C# callable nodes" in str(ei.value) and "T1" in str(ei.value)


def test_graph_preflight_passes_with_enough_nodes_and_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)
    # enough C# nodes AND a fresh/resolved target -> no PreflightError raised
    preflight.run_graph_preflight([_TRAP], None, out_dir=tmp_path,
                                  assess_fn=_fresh_payload, setup_graph_fn=None,
                                  node_count_fn=lambda p: {"csharp_callable": 700})


def test_graph_preflight_checks_safe_task_when_language_tier_is_required(tmp_path, monkeypatch):
    safe_full = TaskSpec(
        "BSEM", "d", ("a.ts",), "safe", ("a.ts",), "none", False,
        language="typescript", required_language_tier="full",
    )
    calls: list[str] = []

    def _assess(_repo_path, spec):
        calls.append(spec.task_id)
        payload = _payload("fresh", "location")
        payload["graph_provenance"] = {
            "language_capability": {"language": "csharp", "tier": "partial"}
        }
        return payload

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)

    with pytest.raises(preflight.PreflightError, match="requires language tier full"):
        preflight.run_graph_preflight([safe_full], None, out_dir=tmp_path,
                                      assess_fn=_assess, setup_graph_fn=None,
                                      node_count_fn=lambda p: {"csharp_callable": 0},
                                      capability_fn=lambda p: {"measured": [{
                                          "language": "typescript", "tier": "partial",
                                          "node_count": 12,
                                      }]})

    assert calls == ["BSEM"]


def test_graph_preflight_uses_language_node_count_for_non_csharp_tiered_task(tmp_path, monkeypatch):
    ts_full = TaskSpec(
        "TSSEM", "d", ("a.ts",), "safe", ("a.ts",), "none", False,
        required_language_tier="full",
    )

    def _assess(_repo_path, _spec):
        payload = _payload("fresh", "location")
        payload["graph_provenance"] = {
            "language_capability": {
                "language": "typescript", "tier": "full", "node_count": 12,
            }
        }
        return payload

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)

    preflight.run_graph_preflight([ts_full], None, out_dir=tmp_path,
                                  assess_fn=_assess, setup_graph_fn=None,
                                  node_count_fn=lambda p: {"csharp_callable": 0})


def test_graph_preflight_writes_coverage_artifact(tmp_path, monkeypatch):
    # Additive observability: run_graph_preflight records the measured per-language capability to
    # out_dir/preflight/coverage.json so the run observatory can render a coverage panel (the
    # language_capability payload is otherwise in-memory only and dropped after the run).
    import json

    ts_full = TaskSpec(
        "TSSEM", "d", ("a.ts",), "safe", ("a.ts",), "none", False,
        required_language_tier="full",
    )

    def _assess(_repo_path, _spec):
        payload = _payload("fresh", "location")
        payload["graph_provenance"] = {
            "language_capability": {
                "language": "typescript", "tier": "full", "node_count": 12,
            }
        }
        return payload

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)

    preflight.run_graph_preflight([ts_full], None, out_dir=tmp_path,
                                  assess_fn=_assess, setup_graph_fn=None,
                                  node_count_fn=lambda p: {"csharp_callable": 0})

    coverage = json.loads((tmp_path / "preflight" / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["by_language"]["typescript"] == {"tier": "full", "node_count": 12}


def test_graph_preflight_fails_non_csharp_tiered_task_with_zero_language_nodes(tmp_path, monkeypatch):
    import json

    ts_full = TaskSpec(
        "TSSEM", "d", ("a.ts",), "safe", ("a.ts",), "none", False,
        required_language_tier="full",
    )

    def _assess(_repo_path, _spec):
        payload = _payload("fresh", "location")
        payload["graph_provenance"] = {
            "language_capability": {
                "language": "typescript", "tier": "full", "node_count": 0,
            }
        }
        return payload

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)

    with pytest.raises(preflight.PreflightError, match="typescript callable nodes"):
        preflight.run_graph_preflight([ts_full], None, out_dir=tmp_path,
                                      assess_fn=_assess, setup_graph_fn=None,
                                      node_count_fn=lambda p: {"csharp_callable": 0})

    coverage = json.loads((tmp_path / "preflight" / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["by_language"]["typescript"] == {"tier": "full", "node_count": 0}


def test_graph_preflight_coverage_write_is_best_effort(tmp_path, monkeypatch):
    ts_full = TaskSpec(
        "TSSEM", "d", ("a.ts",), "safe", ("a.ts",), "none", False,
        required_language_tier="full",
    )

    def _assess(_repo_path, _spec):
        payload = _payload("fresh", "location")
        payload["graph_provenance"] = {
            "language_capability": {
                "language": "typescript", "tier": "full", "node_count": 12,
            }
        }
        return payload

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda ext, dest: tmp_path)
    monkeypatch.setattr(preflight.run_artifacts, "atomic_write_json",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    preflight.run_graph_preflight([ts_full], None, out_dir=tmp_path,
                                  assess_fn=_assess, setup_graph_fn=None,
                                  node_count_fn=lambda p: {"csharp_callable": 0})


# ---- revise-safer route calibration -----------------------------------------------------------


def test_revise_safer_calibration_accepts_bad_revise_then_lower_risk_reference(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "MNGAMMA.patch").write_text("bad route", encoding="utf-8")
    (correct_dir / "MNGAMMA.patch").write_text("reference route", encoding="utf-8")

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        return dest

    calls: list[tuple[str, int, str]] = []

    def _assess(_repo_path, _spec, proposed_patch, _db, *, revise_safer_attempt=0):
        assert not _db.exists()
        _db.write_text("assessment persisted", encoding="utf-8")
        calls.append((proposed_patch, revise_safer_attempt, _db.name))
        if proposed_patch == "bad route":
            return {"recommended_decision": "revise_safer", "scores": {"expected_loss": 0.8}}
        return {"recommended_decision": "proceed", "scores": {"expected_loss": 0.1}}

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _clone)

    preflight.run_revise_safer_calibration(
        [_TEST_TRAP],
        None,
        out_dir=tmp_path,
        assess_fn=_assess,
        setup_graph_fn=lambda _repo: None,
        patch_dir=patch_dir,
        correct_patch_dir=correct_dir,
    )

    assert calls == [
        ("bad route", 0, "bad_revise_calibration.db"),
        ("reference route", 0, "reference_revise_calibration.db"),
    ]


def test_revise_safer_calibration_accepts_verified_js_reference_route(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "JS1.patch").write_text("bad route", encoding="utf-8")
    (correct_dir / "JS1.patch").write_text("reference route", encoding="utf-8")

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        return dest

    calls: list[dict[str, object]] = []

    def _assess(
        _repo_path,
        _spec,
        proposed_patch,
        _db,
        *,
        revise_safer_attempt=0,
        max_revise_safer_attempts=1,
        trusted_candidate_verification=None,
    ):
        calls.append({
            "patch": proposed_patch,
            "attempt": revise_safer_attempt,
            "cap": max_revise_safer_attempts,
            "verification": trusted_candidate_verification,
        })
        if proposed_patch == "bad route":
            return {"recommended_decision": "revise_safer", "scores": {"expected_loss": 0.8}}
        assert trusted_candidate_verification["status"] == "passed"
        return {
            "recommended_decision": "proceed",
            "scores": {"expected_loss": 0.8},
            "gates_fired": [{"name": "candidate_verification_passed"}],
        }

    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", _clone)

    preflight.run_revise_safer_calibration(
        [_JS_TRAP],
        None,
        out_dir=tmp_path,
        assess_fn=_assess,
        candidate_verification_fn=lambda _repo, _spec, _patch: {
            "status": "passed",
            "checks": {"candidate_build": "passed"},
            "required_checks": ["candidate_build"],
            "verified_patch_hash": "abc",
        },
        setup_graph_fn=lambda _repo: None,
        patch_dir=patch_dir,
        correct_patch_dir=correct_dir,
    )

    assert calls[0]["attempt"] == 0
    assert calls[1]["attempt"] == 1
    assert calls[1]["cap"] == 2
    assert calls[1]["verification"] is not None


def test_revise_safer_calibration_requires_js_reference_to_hit_gate_7(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "JS1.patch").write_text("bad route", encoding="utf-8")
    (correct_dir / "JS1.patch").write_text("reference route", encoding="utf-8")
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda _external, dest: dest)

    def _assess(_repo_path, _spec, proposed_patch, _db, **_kwargs):
        if proposed_patch == "bad route":
            return {"recommended_decision": "revise_safer", "scores": {"expected_loss": 0.8}}
        return {"recommended_decision": "proceed", "scores": {"expected_loss": 0.8}, "gates_fired": []}

    with pytest.raises(preflight.PreflightError, match="candidate verification gate 7"):
        preflight.run_revise_safer_calibration(
            [_JS_TRAP],
            None,
            out_dir=tmp_path,
            assess_fn=_assess,
            candidate_verification_fn=lambda _repo, _spec, _patch: {
                "status": "passed",
                "checks": {"candidate_build": "passed"},
                "required_checks": ["candidate_build"],
                "verified_patch_hash": "abc",
            },
            setup_graph_fn=lambda _repo: None,
            patch_dir=patch_dir,
            correct_patch_dir=correct_dir,
        )


def test_revise_safer_calibration_fails_when_no_risky_patch_pair_checked(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda _external, dest: dest)

    with pytest.raises(preflight.PreflightError, match="validated zero risky patch pairs"):
        preflight.run_revise_safer_calibration(
            [_TEST_TRAP],
            None,
            out_dir=tmp_path,
            assess_fn=lambda *a, **k: {"recommended_decision": "proceed", "scores": {"expected_loss": 0}},
            setup_graph_fn=lambda _repo: None,
            patch_dir=patch_dir,
            correct_patch_dir=correct_dir,
        )


def test_revise_safer_calibration_flags_non_revisable_bad_route(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "MNGAMMA.patch").write_text("bad route", encoding="utf-8")
    (correct_dir / "MNGAMMA.patch").write_text("reference route", encoding="utf-8")
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda _external, dest: dest)

    with pytest.raises(preflight.PreflightError, match="expected bad route to return revise_safer"):
        preflight.run_revise_safer_calibration(
            [_TEST_TRAP],
            None,
            out_dir=tmp_path,
            assess_fn=lambda *a, **k: {
                "recommended_decision": "reject",
                "scores": {"expected_loss": 0.8},
            },
            setup_graph_fn=lambda _repo: None,
            patch_dir=patch_dir,
            correct_patch_dir=correct_dir,
        )


def test_revise_safer_calibration_flags_blocked_reference_fix(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "MNGAMMA.patch").write_text("bad route", encoding="utf-8")
    (correct_dir / "MNGAMMA.patch").write_text("reference route", encoding="utf-8")
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda _external, dest: dest)

    def _assess(_repo_path, _spec, proposed_patch, _db, *, revise_safer_attempt=0):
        if proposed_patch == "bad route":
            return {"recommended_decision": "revise_safer", "scores": {"expected_loss": 0.8}}
        return {"recommended_decision": "revise_safer", "scores": {"expected_loss": 0.2}}

    with pytest.raises(preflight.PreflightError, match="reference route remained blocked"):
        preflight.run_revise_safer_calibration(
            [_TEST_TRAP],
            None,
            out_dir=tmp_path,
            assess_fn=_assess,
            setup_graph_fn=lambda _repo: None,
            patch_dir=patch_dir,
            correct_patch_dir=correct_dir,
        )


def test_revise_safer_calibration_flags_reference_that_does_not_lower_loss(tmp_path, monkeypatch):
    patch_dir = tmp_path / "patches"
    correct_dir = tmp_path / "correct"
    patch_dir.mkdir()
    correct_dir.mkdir()
    (patch_dir / "MNGAMMA.patch").write_text("bad route", encoding="utf-8")
    (correct_dir / "MNGAMMA.patch").write_text("reference route", encoding="utf-8")
    monkeypatch.setattr(preflight.rs, "clone_at_recorded_head", lambda _external, dest: dest)

    def _assess(_repo_path, _spec, proposed_patch, _db, *, revise_safer_attempt=0):
        if proposed_patch == "bad route":
            return {"recommended_decision": "revise_safer", "scores": {"expected_loss": 0.8}}
        return {"recommended_decision": "proceed", "scores": {"expected_loss": 0.9}}

    with pytest.raises(preflight.PreflightError, match="reference route did not lower expected_loss"):
        preflight.run_revise_safer_calibration(
            [_TEST_TRAP],
            None,
            out_dir=tmp_path,
            assess_fn=_assess,
            setup_graph_fn=lambda _repo: None,
            patch_dir=patch_dir,
            correct_patch_dir=correct_dir,
        )
