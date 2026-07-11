"""P0 — the shared patch-materialization recipe (extracted/generalized from the old benefit adapter).

Real git in a temp dir (no mocks): git-init a throwaway tree, seed the CURRENT before-content, apply the
patch VERBATIM (-p1 then -p0), read back after-content. Fail-closed to None on any apply failure — never
a partial materialization. A None before-value means "file did not exist before" (not seeded); a None
after-value means "the patch deleted it".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pebra.adapters.patch_materializer import materialize_patch

_MODIFY = (
    "diff --git a/src/a.txt b/src/a.txt\n"
    "--- a/src/a.txt\n"
    "+++ b/src/a.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def test_clean_modify_returns_after_content() -> None:
    after = materialize_patch({"src/a.txt": "old\n"}, _MODIFY)
    assert after == {"src/a.txt": "new\n"}


def test_patch_that_does_not_apply_fails_closed_to_none() -> None:
    # before content does not match the patch context -> git apply fails -> None (never partial)
    assert materialize_patch({"src/a.txt": "totally different\n"}, _MODIFY) is None


def test_new_file_before_none_is_created() -> None:
    create = (
        "diff --git a/src/new.txt b/src/new.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+hello\n"
    )
    after = materialize_patch({"src/new.txt": None}, create)
    assert after == {"src/new.txt": "hello\n"}


def test_deleted_file_reads_back_as_none() -> None:
    delete = (
        "diff --git a/src/gone.txt b/src/gone.txt\n"
        "deleted file mode 100644\n"
        "--- a/src/gone.txt\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-bye\n"
    )
    after = materialize_patch({"src/gone.txt": "bye\n"}, delete)
    assert after == {"src/gone.txt": None}


def test_unsafe_before_keys_fail_closed_before_any_write(tmp_path) -> None:
    # A `..`/absolute BEFORE key must be rejected BEFORE the seed write (root / "../x" would escape the
    # temp dir at write_text time — git apply only guards the patch's own paths, not our seed/read-back).
    for unsafe in (
        "../escape.txt", "/abs/escape.txt", "a/../../escape.txt",
        "C:/abs.txt", "D:evil.py", r"E:\abs\evil.py",  # drive-absolute AND drive-relative (Windows)
    ):
        assert materialize_patch({unsafe: "x\n"}, "diff --git a/x b/x\n") is None
    # belt-and-suspenders: nothing got written into tmp_path's parent as a side effect
    assert not (tmp_path.parent / "escape.txt").exists()


def test_unchanged_is_not_special_cased_here() -> None:
    # the recipe returns after-content faithfully; the "changed nothing -> None" policy belongs to the
    # caller (RCA), not the shared primitive.
    noop = (
        "diff --git a/src/a.txt b/src/a.txt\n--- a/src/a.txt\n+++ b/src/a.txt\n"
        "@@ -1,2 +1,2 @@\n old\n-old\n+old\n"
    )
    # a patch that leaves content identical still round-trips (not forced to None here)
    after = materialize_patch({"src/a.txt": "old\nold\n"}, noop)
    assert after == {"src/a.txt": "old\nold\n"}


def test_patch_bytes_are_written_without_newline_translation(monkeypatch, tmp_path) -> None:
    # Windows text-mode writes translate \n -> \r\n and can turn CRLF input into CRCRLF. The shared
    # materializer is the base of hash-bound candidate verification, so the bytes handed to git apply
    # must be exactly patch.encode("utf-8").
    captured: dict[str, bytes] = {}

    def fake_git_init(cwd: Path) -> bool:
        return True

    def fake_git_apply(cwd: Path, patch_file: Path, *, apply_dir: str = ".") -> bool:
        assert apply_dir == "."
        captured["patch"] = patch_file.read_bytes()
        (cwd / "src" / "a.txt").write_bytes(b"new\r\n")
        return True

    monkeypatch.setattr("pebra.adapters.patch_materializer._git_init", fake_git_init)
    monkeypatch.setattr("pebra.adapters.patch_materializer._git_apply", fake_git_apply)

    patch = _MODIFY.replace("\n", "\r\n")
    after = materialize_patch({"src/a.txt": "old\r\n"}, patch)

    assert captured["patch"] == patch.encode("utf-8")
    assert after == {"src/a.txt": "new\r\n"}


def test_patch_payload_file_does_not_collide_with_repo_file() -> None:
    patch = (
        "diff --git a/__pebra__.patch b/__pebra__.patch\n"
        "--- a/__pebra__.patch\n"
        "+++ b/__pebra__.patch\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    assert materialize_patch({"__pebra__.patch": "old\n"}, patch) == {"__pebra__.patch": "new\n"}


@pytest.mark.parametrize("unsafe", ["C:evil.py", "src/a.py:ads"])
def test_patch_level_windows_unsafe_paths_fail_closed(unsafe: str) -> None:
    patch = (
        f"diff --git a/{unsafe} b/{unsafe}\n"
        f"--- a/{unsafe}\n"
        f"+++ b/{unsafe}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    assert materialize_patch({unsafe: "old\n"}, patch) is None
