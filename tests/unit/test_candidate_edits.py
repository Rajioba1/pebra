from __future__ import annotations

from pathlib import Path

import pytest

from pebra.adapters.candidate_edits import build_candidate_patch
from pebra.adapters.patch_materializer import materialize_patch


def test_build_candidate_patch_is_deterministic_multifile_and_read_only(tmp_path: Path) -> None:
    first = tmp_path / "src" / "first.ts"
    second = tmp_path / "src" / "second.ts"
    first.parent.mkdir()
    first.write_text("export const oldName = 1;\n", encoding="utf-8")
    second.write_text("import { oldName } from './first';\n", encoding="utf-8")
    edits = [
        {"path": "src/first.ts", "old_string": "oldName", "new_string": "newName"},
        {"path": "src/second.ts", "old_string": "oldName", "new_string": "newName"},
    ]

    result = build_candidate_patch(tmp_path, edits)

    assert result.expected_files == ("src/first.ts", "src/second.ts")
    assert result.patch == build_candidate_patch(tmp_path, edits).patch
    assert "diff --git a/src/first.ts b/src/first.ts" in result.patch
    assert "diff --git a/src/second.ts b/src/second.ts" in result.patch
    assert "-export const oldName = 1;" in result.patch
    assert "+export const newName = 1;" in result.patch
    assert materialize_patch({
        "src/first.ts": "export const oldName = 1;\n",
        "src/second.ts": "import { oldName } from './first';\n",
    }, result.patch) == {
        "src/first.ts": "export const newName = 1;\n",
        "src/second.ts": "import { newName } from './first';\n",
    }
    assert first.read_text(encoding="utf-8") == "export const oldName = 1;\n"
    assert second.read_text(encoding="utf-8") == "import { oldName } from './first';\n"


def test_build_candidate_patch_requires_unique_match_unless_replace_all(tmp_path: Path) -> None:
    source = tmp_path / "a.ts"
    source.write_text("old old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="matched 2 times"):
        build_candidate_patch(
            tmp_path,
            [{"path": "a.ts", "old_string": "old", "new_string": "new"}],
        )

    result = build_candidate_patch(
        tmp_path,
        [{
            "path": "a.ts",
            "old_string": "old",
            "new_string": "new",
            "replace_all": True,
        }],
    )
    assert "+new new" in result.patch


@pytest.mark.parametrize("path", ["../outside.ts", "C:outside.ts", "a.ts:stream"])
def test_build_candidate_patch_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError, match="unsafe candidate edit path"):
        build_candidate_patch(
            tmp_path,
            [{"path": path, "old_string": "old", "new_string": "new"}],
        )


def test_build_candidate_patch_rejects_missing_or_empty_match(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text("value\n", encoding="utf-8")

    with pytest.raises(ValueError, match="old_string must be non-empty"):
        build_candidate_patch(
            tmp_path,
            [{"path": "a.ts", "old_string": "", "new_string": "new"}],
        )
    with pytest.raises(ValueError, match="matched 0 times"):
        build_candidate_patch(
            tmp_path,
            [{"path": "a.ts", "old_string": "missing", "new_string": "new"}],
        )
