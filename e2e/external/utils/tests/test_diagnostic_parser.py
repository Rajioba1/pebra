"""TDD unit tests for the dotnet-diagnostic parser (Phase 1 attribution, e2e-side only).

Pure stdlib; no pebra import, no real repo. These prove: structured extraction of file/line/col/code,
symbol extraction for the contract-break codes (CS0535/CS7036), interface-TYPE derivation (the
class/interface-level signal that the implements edge is resolved against), repo-relative POSIX
normalization, and — the honesty canary — that the baseline delta genuinely excludes pre-existing
diagnostics.
"""

from __future__ import annotations

from e2e.external.utils import diagnostic_parser as dp

_REPO = r"C:\work\repo"
# A realistic Roslyn CS0535 line, including the trailing [..csproj] MSBuild suffix dotnet appends.
_CS0535 = (
    r"C:\work\repo\src\App\ViewModels\WorkspaceViewModel.cs(9,20): error CS0535: "
    "'WorkspaceViewModel' does not implement interface member 'IWorkspace.CanCloseAsync()' "
    r"[C:\work\repo\src\App\App.csproj]"
)
_CS7036 = (
    r"C:\work\repo\src\App\WorkspaceService.cs(42,16): error CS7036: There is no argument given "
    "that corresponds to the required formal parameter 'cancellationToken' of "
    "'IWorkspace.CanCloseAsync(CancellationToken)' "
    r"[C:\work\repo\src\App\App.csproj]"
)
_CS1002 = r"C:\work\repo\src\App\Broken.cs(3,1): error CS1002: ; expected [C:\work\repo\src\App\App.csproj]"


def test_parse_cs0535_extracts_file_line_col_code_and_symbols():
    [d] = dp.parse_diagnostics(_CS0535, _REPO)
    assert d.file == "src/App/ViewModels/WorkspaceViewModel.cs"
    assert d.line == 9
    assert d.col == 20
    assert d.code == "CS0535"
    assert d.broken_symbol == "WorkspaceViewModel"
    assert d.contract_symbol == "IWorkspace.CanCloseAsync()"


def test_cs0535_derives_interface_type_for_implements_edge():
    # The class/interface-level signal: the implements edge is WorkspaceViewModel -> IWorkspace,
    # so the parser must expose the interface TYPE (member stripped), not just the member string.
    [d] = dp.parse_diagnostics(_CS0535, _REPO)
    assert d.contract_type == "IWorkspace"


def test_parse_cs7036_extracts_contract_and_leaves_broken_none():
    [d] = dp.parse_diagnostics(_CS7036, _REPO)
    assert d.code == "CS7036"
    # CS7036 is a caller-side break; the broken symbol is the call site, not cleanly named -> None.
    assert d.broken_symbol is None
    assert d.contract_symbol == "IWorkspace.CanCloseAsync(CancellationToken)"
    assert d.contract_type == "IWorkspace"


def test_msbuild_project_suffix_is_stripped_from_message():
    [d] = dp.parse_diagnostics(_CS0535, _REPO)
    assert ".csproj" not in d.message
    assert d.message.endswith("'IWorkspace.CanCloseAsync()'")


def test_file_path_normalized_to_posix_repo_relative():
    line = r"C:\work\repo\src\File.cs(1,1): error CS0001: boom"
    [d] = dp.parse_diagnostics(line, _REPO)
    assert d.file == "src/File.cs"  # drive + repo prefix stripped, backslashes -> forward slashes


def test_file_path_normalization_is_case_insensitive_on_prefix():
    # dotnet may emit a differently-cased drive/prefix than the repo_root we hold.
    line = r"c:\WORK\Repo\src\File.cs(1,1): error CS0001: boom"
    [d] = dp.parse_diagnostics(line, _REPO)
    assert d.file == "src/File.cs"


def test_stderr_combined_output_is_parsed():
    combined = _CS0535 + "\n" + _CS7036
    diags = dp.parse_diagnostics(combined, _REPO)
    assert {d.code for d in diags} == {"CS0535", "CS7036"}


def test_unknown_code_produces_none_symbols():
    [d] = dp.parse_diagnostics(_CS1002, _REPO)
    assert d.code == "CS1002"
    assert d.broken_symbol is None
    assert d.contract_symbol is None
    assert d.contract_type is None


def test_empty_output_returns_empty_list():
    assert dp.parse_diagnostics("", _REPO) == []


def test_non_error_lines_not_parsed():
    noise = (
        "Build succeeded.\n"
        r"C:\work\repo\src\File.cs(1,1): warning CS0108: hides inherited member [x.csproj]" "\n"
        "    0 Warning(s)\n"
    )
    assert dp.parse_diagnostics(noise, _REPO) == []


def test_diagnostics_as_keyset_uses_file_line_col_code():
    diags = dp.parse_diagnostics(_CS0535 + "\n" + _CS7036, _REPO)
    keys = dp.diagnostics_as_keyset(diags)
    assert ("src/App/ViewModels/WorkspaceViewModel.cs", 9, 20, "CS0535") in keys
    assert ("src/App/WorkspaceService.cs", 42, 16, "CS7036") in keys


def test_delta_excludes_baseline_diagnostic():
    # HONESTY CANARY: a pre-existing diagnostic (present in the baseline) must NOT be attributed to the
    # edit. Only genuinely-new diagnostics survive the delta.
    baseline = dp.parse_diagnostics(_CS1002, _REPO)  # pre-existing, unrelated to the edit
    post = dp.parse_diagnostics(_CS1002 + "\n" + _CS0535, _REPO)  # same pre-existing + one NEW
    delta = dp.compute_delta(post, dp.diagnostics_as_keyset(baseline))
    codes = {d.code for d in delta}
    assert codes == {"CS0535"}  # the pre-existing CS1002 is filtered out; only the new CS0535 remains


def test_delta_against_empty_baseline_returns_all():
    post = dp.parse_diagnostics(_CS0535, _REPO)
    delta = dp.compute_delta(post, frozenset())
    assert [d.code for d in delta] == ["CS0535"]
