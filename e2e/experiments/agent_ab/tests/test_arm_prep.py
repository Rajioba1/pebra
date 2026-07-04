"""oracle-positive pre-patch: git-apply the known correct fix into a clone before the agent runs."""

from __future__ import annotations

import subprocess

import pytest

from e2e.experiments.agent_ab.runners import arm_prep
from e2e.experiments.agent_ab.runners.run_pair import RunPairError


def _git(tmp_path, *args):
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True, text=True)


def _repo_with_committed_file(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")


def _real_patch(tmp_path, new_content: str) -> str:
    # generate a real git patch (old -> new), then revert so the patch can be re-applied.
    (tmp_path / "a.txt").write_text(new_content, encoding="utf-8")
    patch = subprocess.run(["git", "-C", str(tmp_path), "diff"], capture_output=True, text=True).stdout
    _git(tmp_path, "checkout", "--", "a.txt")
    return patch


def test_prepare_oracle_patch_applies_correct_fix(tmp_path):
    _repo_with_committed_file(tmp_path)
    pdir = tmp_path / "patches"
    pdir.mkdir()
    (pdir / "T1.patch").write_text(_real_patch(tmp_path, "new\n"), encoding="utf-8")
    applied = arm_prep.prepare_oracle_patch(tmp_path, "T1", patch_dir=pdir)
    assert applied.name == "T1.patch"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new\n"  # correct state pre-applied


def test_missing_patch_raises_at_setup(tmp_path):
    _repo_with_committed_file(tmp_path)
    with pytest.raises(RunPairError, match="no correct-fix patch"):
        arm_prep.prepare_oracle_patch(tmp_path, "NOPE", patch_dir=tmp_path / "empty")


def test_non_applying_patch_raises(tmp_path):
    _repo_with_committed_file(tmp_path)
    pdir = tmp_path / "patches"
    pdir.mkdir()
    # a patch against a file that doesn't exist -> git apply fails
    (pdir / "T1.patch").write_text(
        "diff --git a/missing.txt b/missing.txt\n--- a/missing.txt\n+++ b/missing.txt\n"
        "@@ -1 +1 @@\n-nope\n+yes\n", encoding="utf-8")
    with pytest.raises(RunPairError, match="did not apply cleanly"):
        arm_prep.prepare_oracle_patch(tmp_path, "T1", patch_dir=pdir)
