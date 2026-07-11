"""Post-agent hidden-oracle evaluator: injection is post-hoc; test runs only if a test PROJECT was
injected and the build passes; tests target the injected .csproj directly (no fabricated pass)."""

from __future__ import annotations

from types import SimpleNamespace

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import evaluator


def _b(passed):
    return SimpleNamespace(ran=True, passed=passed, error_summary="")


def _build_pass(_p):
    return _b(True)


def _build_fail(_p):
    return _b(False)


def _test_pass(_repo, *, project=None):
    return _b(True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _ev_dir_with_project(tmp_path, task_id):
    """A task evaluator dir that DOES ship a test project (a .csproj)."""
    ev_dir = tmp_path / "evtests"
    proj = ev_dir / task_id / "Evaluator"
    proj.mkdir(parents=True)
    (proj / "Evaluator.csproj").write_text("<Project></Project>")
    (proj / "Tests.cs").write_text("// evaluator test")
    return ev_dir


def _ev_dir_no_project(tmp_path, task_id):
    """A task evaluator dir that exists but ships NO .csproj — must NOT fabricate a test pass."""
    ev_dir = tmp_path / "evtests"
    (ev_dir / task_id).mkdir(parents=True)
    (ev_dir / task_id / "notes.md").write_text("no project here")
    return ev_dir


def test_no_evaluator_dir_build_only(tmp_path):
    ev_dir = tmp_path / "evtests"  # no task subdir at all
    build, test, injected = evaluator.run_evaluator(
        _repo(tmp_path), "T1", evaluator_dir=ev_dir, build_fn=_build_pass, test_fn=_test_pass)
    assert injected is False and test is None and build.passed is True


def test_injected_project_tests_run_against_that_project(tmp_path):
    repo = _repo(tmp_path)
    ev_dir = _ev_dir_with_project(tmp_path, "T1")
    seen: list = []

    def _capture_test(_repo, *, project=None):
        seen.append(project)
        return _b(True)

    build, test, injected = evaluator.run_evaluator(
        repo, "T1", evaluator_dir=ev_dir, build_fn=_build_pass, test_fn=_capture_test)
    assert injected is True
    assert (repo / "Evaluator" / "Evaluator.csproj").exists()  # copied post-agent
    assert test is not None and len(seen) == 1
    # tests were targeted at the injected .csproj (not the solution) — closes the fabricated-pass trap
    assert seen[0] is not None and seen[0].name == "Evaluator.csproj"


def test_injected_dir_without_project_does_not_fabricate_pass(tmp_path):
    repo = _repo(tmp_path)
    ev_dir = _ev_dir_no_project(tmp_path, "T1")

    def _must_not_run(_repo, *, project=None):
        raise AssertionError("test_fn must not run when no .csproj was injected")

    build, test, injected = evaluator.run_evaluator(
        repo, "T1", evaluator_dir=ev_dir, build_fn=_build_pass, test_fn=_must_not_run)
    # A dir with no test project = honest no-signal, NOT test_passed=True.
    assert injected is False and test is None and build.passed is True
    assert not (repo / "notes.md").exists()


def test_injected_but_build_fails_skips_tests(tmp_path):
    repo = _repo(tmp_path)
    ev_dir = _ev_dir_with_project(tmp_path, "T1")
    build, test, injected = evaluator.run_evaluator(
        repo, "T1", evaluator_dir=ev_dir, build_fn=_build_fail, test_fn=_test_pass)
    assert injected is True and test is None and build.passed is False


def test_existing_repo_test_filter_runs_without_injection(tmp_path):
    repo = _repo(tmp_path)
    project = repo / "src" / "Numerics.Tests" / "Numerics.Tests.csproj"
    project.parent.mkdir(parents=True)
    project.write_text("<Project />")
    spec = TaskSpec(
        "MNGAMMA", "d", ("src/Numerics/SpecialFunctions/Gamma.cs",), "risky",
        ("src/Numerics/SpecialFunctions/Gamma.cs",), "test_failure", False,
        evaluator_test_project="src/Numerics.Tests/Numerics.Tests.csproj",
        evaluator_test_filter="FullyQualifiedName~GammaTests",
    )
    seen: list[tuple[object, object]] = []

    def _capture_test(_repo, *, project=None, test_filter=None):
        seen.append((project, test_filter))
        return _b(True)

    build, test, injected = evaluator.run_evaluator(
        repo, spec, evaluator_dir=tmp_path / "evtests", build_fn=_build_pass, test_fn=_capture_test)

    assert build.passed is True and test is not None
    assert injected is False
    assert seen == [(project, "FullyQualifiedName~GammaTests")]


def test_typescript_existing_repo_test_uses_backend_without_csproj_injection(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.test.ts").write_text("test('existing', () => {});", encoding="utf-8")
    spec = TaskSpec(
        "JS1", "d", ("src/a.ts",), "risky", ("src/a.ts",), "test_failure", False,
        evaluator_test_project="src/a.test.ts",
        evaluator_test_filter="handles safe route",
        language="typescript",
    )
    seen = {}

    class FakeBackend:
        def run_build(self, repo_path, spec_arg):
            seen["build_language"] = spec_arg.language
            return _b(True)

        def run_tests(self, repo_path, spec_arg, *, project=None, test_filter=None):
            seen["test"] = (spec_arg.language, project, test_filter)
            return _b(True)

    monkeypatch.setattr(evaluator.backends, "backend_for_spec", lambda spec_arg: FakeBackend())

    build, test, injected = evaluator.run_evaluator(repo, spec, evaluator_dir=tmp_path / "evtests")

    assert build.passed is True and test is not None
    assert injected is False
    assert seen == {
        "build_language": "typescript",
        "test": ("typescript", repo / "src/a.test.ts", "handles safe route"),
    }


def test_typescript_hidden_test_is_injected_only_after_subject_run(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    test_rel = "packages/zod/src/v3/tests/schema-type-label.test.ts"
    spec = TaskSpec(
        "JS1", "d", ("src/a.ts",), "risky", ("src/a.ts",), "build_failure", True,
        evaluator_test_project=test_rel, language="typescript", specimen="javascript",
    )
    evaluator_dir = tmp_path / "evaluator_tests"
    hidden = evaluator_dir / "JS1" / test_rel
    hidden.parent.mkdir(parents=True)
    hidden.write_text("test('hidden', () => {});", encoding="utf-8")
    seen = {}
    assert not (repo / test_rel).exists()

    class FakeBackend:
        def run_build(self, repo_path, spec_arg):
            return _b(True)

        def run_tests(self, repo_path, spec_arg, *, project=None, test_filter=None):
            seen["project"] = project
            return _b(True)

    monkeypatch.setattr(evaluator.backends, "backend_for_spec", lambda spec_arg: FakeBackend())

    build, test, injected = evaluator.run_evaluator(repo, spec, evaluator_dir=evaluator_dir)

    assert build.passed is True and test is not None
    assert injected is True
    assert seen["project"] == repo / test_rel
    assert (repo / test_rel).read_text(encoding="utf-8") == "test('hidden', () => {});"
