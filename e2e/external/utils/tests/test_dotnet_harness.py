"""TDD unit tests for the dotnet_harness diagnostic extension (Phase 1 attribution).

Prove the additions are purely additive (existing DotNetBuildResult construction unaffected), that the
delta augmentation wires parse+delta correctly, and that run_build_delta skips honestly when the SDK is
absent. The subprocess/build path itself is exercised in the gated E2E lane. No pebra import.
"""

from __future__ import annotations

from e2e.external.utils import dotnet_harness as dn

_REPO = r"C:\work\repo"
_CS0535 = (
    r"C:\work\repo\src\App\WorkspaceViewModel.cs(9,20): error CS0535: "
    "'WorkspaceViewModel' does not implement interface member 'IWorkspace.CanCloseAsync()'"
)
_CS1002 = r"C:\work\repo\src\App\Broken.cs(3,1): error CS1002: ; expected"


def test_result_new_diagnostic_fields_default_empty():
    # legacy positional construction (as CompilerOutcomeState builder uses) must still work
    r = dn.DotNetBuildResult(True, True, False, 1, "err", 1.2)
    assert r.structured_diagnostics == []
    assert r.delta_diagnostics == []


def test_augment_populates_structured_and_delta_excluding_baseline():
    # a pre-existing CS1002 is in the baseline; a new CS0535 is not
    import e2e.external.utils.diagnostic_parser as dp

    baseline_keys = dp.diagnostics_as_keyset(dp.parse_diagnostics(_CS1002, _REPO))
    output = _CS1002 + "\n" + _CS0535
    structured, delta = dn.augment_with_diagnostics(output, _REPO, baseline_keys)
    assert {d.code for d in structured} == {"CS1002", "CS0535"}
    assert [d.code for d in delta] == ["CS0535"]  # baseline CS1002 filtered out


def test_run_build_delta_skips_when_dotnet_absent(monkeypatch):
    monkeypatch.setattr(dn, "dotnet_available", lambda: False)
    r = dn.run_build_delta(_REPO, baseline_keys=frozenset())
    assert r.available is False
    assert r.ran is False
    assert r.structured_diagnostics == []
    assert r.delta_diagnostics == []


def test_run_build_summary_keeps_enough_errors_for_multi_implementer_tasks(monkeypatch):
    class Proc:
        returncode = 1
        stdout = "\n".join(f"file{i}.cs(1,1): error CS0535: missing member {i}" for i in range(25))
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)
    monkeypatch.setattr(dn.subprocess, "run", lambda *a, **k: Proc())
    r = dn.run_build(_REPO)
    assert len(r.error_summary.splitlines()) == 20


def test_run_build_resolves_repo_root_before_invoking_dotnet(monkeypatch, tmp_path):
    seen = {}

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)

    def _run(args, **kwargs):
        seen["args"] = args
        seen["cwd"] = kwargs["cwd"]
        return Proc()

    monkeypatch.setattr(dn.subprocess, "run", _run)
    monkeypatch.chdir(tmp_path.parent)
    rel = tmp_path.relative_to(tmp_path.parent)
    dn.run_build(rel)

    assert seen["cwd"] == str(tmp_path.resolve())
    assert seen["args"][2] == str(tmp_path.resolve() / "TemplateBlueprint.sln")


def test_run_tests_summary_keeps_more_than_five_failures(monkeypatch):
    class Proc:
        returncode = 1
        stdout = "\n".join(f"Test {i} Failed" for i in range(20))
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)
    monkeypatch.setattr(dn.subprocess, "run", lambda *a, **k: Proc())
    r = dn.run_tests(_REPO)
    assert len(r.error_summary.splitlines()) == 15


def test_run_tests_passes_filter_to_dotnet(monkeypatch):
    seen = {}

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)

    def _run(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return Proc()

    monkeypatch.setattr(dn.subprocess, "run", _run)
    dn.run_tests(_REPO, project=r"C:\work\repo\tests\Tests.csproj", test_filter="FullyQualifiedName~GammaTests")

    assert "--filter" in seen["args"]
    assert seen["args"][-1] == "FullyQualifiedName~GammaTests"
    assert seen["env"]["DOTNET_CLI_UI_LANGUAGE"] == "en"


def test_targeted_run_tests_marks_zero_selected_tests_as_failed(monkeypatch):
    class Proc:
        returncode = 0
        stdout = "Passed!  - Failed: 0, Passed: 0, Skipped: 0, Total: 0, Duration: 1 ms"
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)
    monkeypatch.setattr(dn.subprocess, "run", lambda *a, **k: Proc())

    r = dn.run_tests(
        _REPO,
        project=r"C:\work\repo\tests\Tests.csproj",
        test_filter="FullyQualifiedName~MissingTests",
    )

    assert r.tests_selected == 0
    assert r.passed is False


def test_untargeted_run_tests_keeps_exit_code_semantics_for_zero_tests(monkeypatch):
    class Proc:
        returncode = 0
        stdout = "Passed!  - Failed: 0, Passed: 0, Skipped: 0, Total: 0, Duration: 1 ms"
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)
    monkeypatch.setattr(dn.subprocess, "run", lambda *a, **k: Proc())

    r = dn.run_tests(_REPO)

    assert r.tests_selected == 0
    assert r.passed is True


def test_run_tests_sums_all_vstest_total_summaries(monkeypatch):
    class Proc:
        returncode = 0
        stdout = "\n".join([
            "Passed!  - Failed: 0, Passed: 3, Skipped: 0, Total: 3, Duration: 1 ms",
            "Passed!  - Failed: 0, Passed: 4, Skipped: 0, Total: 4, Duration: 1 ms",
        ])
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)
    monkeypatch.setattr(dn.subprocess, "run", lambda *a, **k: Proc())

    r = dn.run_tests(_REPO, project=r"C:\work\repo\tests\Tests.csproj")

    assert r.tests_selected == 7


def test_targeted_run_tests_no_summary_is_not_treated_as_zero(monkeypatch):
    class Proc:
        returncode = 0
        stdout = "Build succeeded."
        stderr = ""

    monkeypatch.setattr(dn, "dotnet_available", lambda: True)
    monkeypatch.setattr(dn.subprocess, "run", lambda *a, **k: Proc())

    r = dn.run_tests(
        _REPO,
        project=r"C:\work\repo\tests\Tests.csproj",
        test_filter="FullyQualifiedName~GammaTests",
    )

    assert r.tests_selected is None
    assert r.passed is True
