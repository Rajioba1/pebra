"""Destructive-op slice, Phase 2 — git diff header parsing (pure regex, no I/O).

Detects file-level ops (DELETE/CREATE/RENAME/MOVE) from `diff --git` header blocks — distinct from
hunk-body parsing. RENAME vs MOVE is decided by whether the parent directory changed. Ordinary modify
patches yield no entry (so the assess path is inert for normal edits — golden stays byte-identical).
"""

from __future__ import annotations

from pebra.adapters.patch_header_adapter import DestructiveOp, parse_patch_headers

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
