from __future__ import annotations

import os
import inspect
import subprocess
from pathlib import Path

import pytest

from pebra.adapters.candidate_application import (
    CandidateApplicationAdapter,
    CandidateApplicationError,
)
from pebra.adapters import candidate_binding


_MULTI_PATCH = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1 +1 @@\n"
    "-old-a\n"
    "+new-a\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "--- a/src/b.py\n"
    "+++ b/src/b.py\n"
    "@@ -1 +1 @@\n"
    "-old-b\n"
    "+new-b\n"
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("old-a\n", encoding="utf-8")
    (tmp_path / "src/b.py").write_text("old-b\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_application_applies_complete_multi_file_candidate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    changed = CandidateApplicationAdapter().apply(
        repo, _MULTI_PATCH, expected_files=("src/a.py", "src/b.py")
    )

    assert changed == ("src/a.py", "src/b.py")
    assert (repo / "src/a.py").read_text(encoding="utf-8") == "new-a\n"
    assert (repo / "src/b.py").read_text(encoding="utf-8") == "new-b\n"


def test_application_normalizes_validated_envelope_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    changed = CandidateApplicationAdapter().apply(
        repo,
        _MULTI_PATCH,
        expected_files=("./src/a.py", "src\\b.py"),
    )

    assert changed == ("src/a.py", "src/b.py")


def test_application_preserves_existing_filename_case(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    camel = repo / "src/parseUtil.ts"
    camel.write_text("old-name\n", encoding="utf-8")
    patch = (
        "diff --git a/src/parseUtil.ts b/src/parseUtil.ts\n"
        "--- a/src/parseUtil.ts\n"
        "+++ b/src/parseUtil.ts\n"
        "@@ -1 +1 @@\n"
        "-old-name\n"
        "+new-name\n"
    )

    changed = CandidateApplicationAdapter().apply(
        repo, patch, expected_files=("src/parseUtil.ts",)
    )

    assert changed == ("src/parseUtil.ts",)
    assert {path.name for path in (repo / "src").iterdir()} >= {"parseUtil.ts"}
    assert camel.read_text(encoding="utf-8") == "new-name\n"


def test_candidate_application_requires_validated_expected_files() -> None:
    parameter = inspect.signature(CandidateApplicationAdapter.apply).parameters[
        "expected_files"
    ]

    assert parameter.default is inspect.Parameter.empty


def test_case_only_rename_is_explicitly_unsupported_by_binding_v1() -> None:
    patch = (
        "diff --git a/src/parseUtil.ts b/src/parseutil.ts\n"
        "similarity index 100%\n"
        "rename from src/parseUtil.ts\n"
        "rename to src/parseutil.ts\n"
    )

    assert candidate_binding._has_unsupported_metadata(patch) is True


def test_application_accepts_later_file_with_unquoted_spaces(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    spaced = repo / "src/user guide.py"
    spaced.write_text("old-guide\n", encoding="utf-8")
    patch = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old-a\n+new-a\n"
        "diff --git a/src/user guide.py b/src/user guide.py\n"
        "--- a/src/user guide.py\n+++ b/src/user guide.py\n"
        "@@ -1 +1 @@\n-old-guide\n+new-guide\n"
    )

    changed = CandidateApplicationAdapter().apply(
        repo,
        patch,
        expected_files=("src/a.py", "src/user guide.py"),
    )

    assert changed == ("src/a.py", "src/user guide.py")
    assert (repo / "src/a.py").read_text(encoding="utf-8") == "new-a\n"
    assert spaced.read_text(encoding="utf-8") == "new-guide\n"


def test_application_rolls_back_all_files_when_replace_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    real_replace = os.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replacement failure")
        real_replace(source, target)

    adapter = CandidateApplicationAdapter(replace_fn=fail_second)

    with pytest.raises(CandidateApplicationError, match="rolled back"):
        adapter.apply(repo, _MULTI_PATCH, expected_files=("src/a.py", "src/b.py"))

    assert (repo / "src/a.py").read_text(encoding="utf-8") == "old-a\n"
    assert (repo / "src/b.py").read_text(encoding="utf-8") == "old-b\n"


def test_application_rejects_unmaterializable_or_escaping_patch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside_name = f"outside-{tmp_path.name}.py"
    escaping = (
        f"*** Begin Patch\n*** Add File: ../{outside_name}\n+bad\n*** End Patch\n"
    )

    with pytest.raises(CandidateApplicationError, match="materialized"):
        CandidateApplicationAdapter().apply(
            repo, escaping, expected_files=(f"../{outside_name}",)
        )

    assert not (tmp_path.parent / outside_name).exists()
    assert (repo / "src/a.py").read_text(encoding="utf-8") == "old-a\n"


def test_application_rejects_materialized_files_outside_validated_envelope(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    patch = (
        "*** Begin Patch\n"
        "*** Add File: src/extra.py\n"
        "+payload\n"
        "*** End Patch\n"
    )

    with pytest.raises(CandidateApplicationError, match="validated candidate envelope"):
        CandidateApplicationAdapter().apply(
            repo, patch, expected_files=("src/a.py",)
        )

    assert not (repo / "src/extra.py").exists()
