"""The canonical repo-relative path-safety predicate (`is_safe_relative`) and its filtering wrapper.

One predicate is the single source of truth for every adapter that reads/writes caller-supplied paths
(radon, bandit, the materialized CodeGraph diff), so an escape-class fix lands in exactly one place.
"""

from __future__ import annotations

from pebra.adapters._paths import is_safe_relative, safe_relative_files


def test_is_safe_relative_accepts_normal_repo_paths(tmp_path):
    assert is_safe_relative(str(tmp_path), "src/A.cs")
    assert is_safe_relative(str(tmp_path), "a/b/c.py")


def test_is_safe_relative_rejects_every_escape_class(tmp_path):
    root = str(tmp_path)
    for bad in (
        "",                 # empty
        "/abs/x",           # posix absolute
        "../escape.py",     # parent traversal
        "a/../../b",        # traversal that escapes
        "C:/abs.txt",       # windows absolute
        "D:evil.py",        # windows drive-relative
        r"E:\abs\evil.py",  # windows absolute (backslash)
        "file.txt:stream",  # NTFS alternate data stream / colon
    ):
        assert not is_safe_relative(root, bad), bad


def test_safe_relative_files_is_the_predicate_applied_as_a_filter(tmp_path):
    root = str(tmp_path)
    files = ["ok.py", "D:evil.py", "sub/ok2.cs", "../nope.py", "ads.txt:zone"]
    assert safe_relative_files(root, files) == ["ok.py", "sub/ok2.cs"]
