"""Destructive-op slice, Phase 2 — git diff header parsing (pure regex, no I/O).

Detects file-level ops (DELETE/CREATE/RENAME/MOVE) from `diff --git` header blocks — distinct from
hunk-body parsing. RENAME vs MOVE is decided by whether the parent directory changed. Ordinary modify
patches yield no entry (so the assess path is inert for normal edits — golden stays byte-identical).
"""

from __future__ import annotations

from pebra.adapters.patch_header_adapter import (
    DestructiveOp,
    parse_patch_headers,
    touched_files,
)

_DELETE = """diff --git a/src/config.py b/src/config.py
deleted file mode 100644
index abc1234..0000000
--- a/src/config.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
"""

_CREATE = """diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+y = 2
+z = 3
"""

_RENAME_SAME_DIR = """diff --git a/src/foo.py b/src/bar.py
similarity index 95%
rename from src/foo.py
rename to src/bar.py
"""

_MOVE_DIFF_DIR = """diff --git a/src/utils/foo.py b/src/lib/foo.py
similarity index 100%
rename from src/utils/foo.py
rename to src/lib/foo.py
"""

_MODIFY = """diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,4 @@
 x = 1
+y = 2
"""


def test_deleted_file_is_delete_op():
    (op,) = parse_patch_headers(_DELETE)
    assert op == DestructiveOp(kind="DELETE", old_path="src/config.py", new_path=None,
                               similarity_index=None)


def test_new_file_is_create_op():
    (op,) = parse_patch_headers(_CREATE)
    assert op.kind == "CREATE"
    assert op.new_path == "src/new.py"
    assert op.old_path is None


def test_rename_same_dir_is_rename():
    (op,) = parse_patch_headers(_RENAME_SAME_DIR)
    assert op.kind == "RENAME"
    assert op.old_path == "src/foo.py"
    assert op.new_path == "src/bar.py"
    assert op.similarity_index == 95


def test_rename_different_dir_is_move():
    (op,) = parse_patch_headers(_MOVE_DIFF_DIR)
    assert op.kind == "MOVE"
    assert op.old_path == "src/utils/foo.py"
    assert op.new_path == "src/lib/foo.py"
    assert op.similarity_index == 100


def test_regular_modify_yields_no_op():
    assert parse_patch_headers(_MODIFY) == []


def test_empty_patch_yields_no_op():
    assert parse_patch_headers("") == []


def test_multiple_ops_in_one_patch():
    ops = parse_patch_headers(_DELETE + _CREATE + _MODIFY)
    kinds = sorted(o.kind for o in ops)
    assert kinds == ["CREATE", "DELETE"]  # modify contributes nothing


def test_a_b_prefixes_stripped():
    (op,) = parse_patch_headers(_DELETE)
    assert not op.old_path.startswith("a/")


# --- touched_files (P0): the file paths a patch touches, for materialization ---


def test_touched_files_modify_returns_the_single_path():
    modify = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    assert touched_files(modify) == ("src/a.py",)


def test_touched_files_rename_returns_both_old_and_new():
    rename = (
        "diff --git a/src/old.py b/src/new.py\n"
        "similarity index 100%\nrename from src/old.py\nrename to src/new.py\n"
    )
    assert touched_files(rename) == ("src/new.py", "src/old.py")  # sorted, deduped, both sides


def test_touched_files_multiple_files_deduped_and_sorted():
    patch = _DELETE + _CREATE + _MODIFY
    assert touched_files(patch) == tuple(sorted(set(touched_files(patch))))
    assert "src/config.py" in touched_files(patch)


def test_touched_files_accepts_multifile_git_diff_with_unquoted_spaces():
    patch = (
        "diff --git a/docs/readme.md b/docs/readme.md\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/docs/readme.md\n+++ b/docs/readme.md\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/docs/x b/y.md b/docs/x b/y.md\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/docs/x b/y.md\n+++ b/docs/x b/y.md\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )

    assert touched_files(patch) == ("docs/readme.md", "docs/x b/y.md")


def test_touched_files_accepts_unquoted_rename_paths_with_spaces():
    patch = (
        "diff --git a/docs/old guide.md b/docs/new guide.md\n"
        "similarity index 100%\n"
        "rename from docs/old guide.md\n"
        "rename to docs/new guide.md\n"
    )

    assert touched_files(patch) == ("docs/new guide.md", "docs/old guide.md")


def test_touched_files_rejects_unparseable_diff_header_without_stale_state():
    patch = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git malformed header\n"
        "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-old\n+new\n"
    )

    assert touched_files(patch) == ()


def test_touched_files_rejects_ambiguous_unquoted_space_header():
    patch = "diff --git a/a b/b b/c\n"

    assert touched_files(patch) == ()


def test_touched_files_empty_patch_is_empty():
    assert touched_files("") == ()


def test_touched_files_accepts_git_quoted_paths():
    patch = (
        'diff --git "a/src/a\\tb.py" "b/src/a\\tb.py"\n'
        '--- "a/src/a\\tb.py"\n+++ "b/src/a\\tb.py"\n@@ -1 +1 @@\n-a\n+b\n'
    )

    assert touched_files(patch) == ("src/a\tb.py",)


def test_touched_files_decodes_git_octal_utf8_path():
    patch = (
        'diff --git "a/src/\\303\\251.py" "b/src/\\303\\251.py"\n'
        '--- "a/src/\\303\\251.py"\n+++ "b/src/\\303\\251.py"\n@@ -1 +1 @@\n-a\n+b\n'
    )

    assert touched_files(patch) == ("src/\u00e9.py",)


def test_touched_files_ignores_header_like_source_lines_inside_hunk():
    patch = (
        "diff --git a/query.sql b/query.sql\n--- a/query.sql\n+++ b/query.sql\n"
        "@@ -1 +1 @@\n--- old comment\n+++ new comment\n"
    )

    assert touched_files(patch) == ("query.sql",)


def test_touched_files_accepts_git_tolerated_bare_empty_context_line():
    patch = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1,3 +1,3 @@\n-old\n+new\n\n tail\n"
    )

    assert touched_files(patch) == ("src/a.py",)


def test_quoted_rename_is_parsed_and_contributes_both_paths():
    patch = (
        'diff --git "a/src/old name.py" "b/src/new name.py"\n'
        "similarity index 100%\n"
        'rename from "src/old name.py"\nrename to "src/new name.py"\n'
    )

    (operation,) = parse_patch_headers(patch)
    assert operation.old_path == "src/old name.py"
    assert operation.new_path == "src/new name.py"
    assert touched_files(patch) == ("src/new name.py", "src/old name.py")
