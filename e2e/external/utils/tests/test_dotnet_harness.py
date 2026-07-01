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
