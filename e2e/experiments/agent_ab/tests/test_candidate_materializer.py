"""P4 — candidate_materializer: REAL git (no mocks). Applies the patch to a scratch copy, never mutates
the source, and fails closed on a non-applying patch."""

from __future__ import annotations

from e2e.experiments.agent_ab.tools import candidate_materializer as cm

_MODIFY = (
    "diff --git a/src/A.cs b/src/A.cs\n--- a/src/A.cs\n+++ b/src/A.cs\n@@ -1 +1 @@\n-old\n+new\n"
)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "A.cs").write_bytes(b"old\n")
    return repo


def test_materialize_applies_to_scratch_without_touching_source(tmp_path):
    repo = _repo(tmp_path)
    scratch = cm.materialize_candidate(repo, _MODIFY)
    assert scratch is not None
    assert (scratch / "src" / "A.cs").read_bytes() == b"new\n"  # patched in the scratch
    assert (repo / "src" / "A.cs").read_bytes() == b"old\n"     # source untouched
    cm.cleanup(scratch)
    assert not scratch.exists()


def test_materialize_non_applying_patch_fails_closed(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "A.cs").write_text("does not match the patch context\n", encoding="utf-8")
    assert cm.materialize_candidate(repo, _MODIFY) is None


def test_materialize_excludes_build_and_vcs_dirs(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".git").mkdir()
    (repo / ".git" / "x").write_text("v", encoding="utf-8")
    (repo / "bin").mkdir()
    (repo / "bin" / "artifact.dll").write_text("bin", encoding="utf-8")
    scratch = cm.materialize_candidate(repo, _MODIFY)
    assert scratch is not None
    assert not (scratch / "bin").exists()  # bin/obj/.git excluded from the scratch copy
    cm.cleanup(scratch)
