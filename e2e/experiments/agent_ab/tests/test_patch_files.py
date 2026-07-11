from e2e.experiments.agent_ab.patch_files import touched_files


def test_touched_files_extracts_multifile_rename_and_quoted_headers():
    patch = (
        "diff --git a/src/a.ts b/src/a.ts\n"
        "diff --git a/src/old.ts b/src/new.ts\n"
        'diff --git "a/src/with space.ts" "b/src/with space.ts"\n'
    )
    assert touched_files(patch) == (
        "src/a.ts",
        "src/new.ts",
        "src/old.ts",
        "src/with space.ts",
    )


def test_touched_files_ignores_malformed_headers():
    assert touched_files('diff --git "unterminated\n') == ()


def test_touched_files_accepts_plain_unified_diff_headers():
    patch = (
        "--- a/packages/zod/src/v3/helpers/parseUtil.ts\n"
        "+++ b/packages/zod/src/v3/helpers/parseUtil.ts\n"
        "@@ -1 +1 @@\n"
        "-export const oldName = 1;\n"
        "+export const newName = 1;\n"
    )

    assert touched_files(patch) == ("packages/zod/src/v3/helpers/parseUtil.ts",)
