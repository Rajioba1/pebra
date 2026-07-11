"""Optional real-Zod proof for the JS/TS corpus.

Set E2E_ZOD_REPO to a Zod checkout with pnpm available to verify the checked-in patches against the
same zshy profile used by the JS specimen. Ordinary CI skips this because it does not own that repo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from e2e.experiments.agent_ab.specimens.javascript.corpus import loader
from e2e.external.utils import node_harness

_CORPUS = Path(loader.__file__).resolve().parent
_ZOD_ENV = "E2E_ZOD_REPO"


def _copy_repo(src: Path, dest: Path) -> Path:
    def _ignore(_dir, names):
        return {name for name in names if name in {"node_modules", "dist", ".codegraph", ".pebra"}}

    shutil.copytree(src, dest, ignore=_ignore)
    return dest


def _apply_patch(repo: Path, patch: Path) -> None:
    proc = subprocess.run(["git", "apply", str(patch)], cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(not os.environ.get(_ZOD_ENV), reason=f"set {_ZOD_ENV} to a Zod checkout")
def test_js1_zshy_oracle_patches_against_real_zod(tmp_path):
    source = Path(os.environ[_ZOD_ENV]).resolve()
    assert (source / "packages" / "zod" / "src" / "v3" / "types.ts").is_file()

    bad = _copy_repo(source, tmp_path / "bad")
    _apply_patch(bad, _CORPUS / "oracle_patches" / "JS1.patch")
    bad_build = node_harness.run_build(
        bad, profile="zshy", selector="zod:tsconfig.build.json", timeout=1200, install_timeout=1200
    )
    assert bad_build.ran and not bad_build.passed

    fixed = _copy_repo(source, tmp_path / "fixed")
    _apply_patch(fixed, _CORPUS / "correct_fix_patches" / "JS1.patch")
    fixed_build = node_harness.run_build(
        fixed, profile="zshy", selector="zod:tsconfig.build.json", timeout=1200, install_timeout=1200
    )
    assert fixed_build.ran and fixed_build.passed, fixed_build.error_summary
    hidden_rel = Path("packages/zod/src/v3/tests/schema-type-label.test.ts")
    hidden = _CORPUS / "evaluator_tests" / "JS1" / hidden_rel
    destination = fixed / hidden_rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hidden, destination)
    fixed_test = node_harness.run_tests(fixed, test_path=hidden_rel, timeout=1200, install_timeout=1200)
    assert fixed_test.ran and fixed_test.passed, fixed_test.error_summary
    assert (fixed_test.tests_selected or 0) > 0


@pytest.mark.skipif(not os.environ.get(_ZOD_ENV), reason=f"set {_ZOD_ENV} to a Zod checkout")
def test_js4_public_helper_compatibility_routes_against_real_zod(tmp_path):
    source = Path(os.environ[_ZOD_ENV]).resolve()
    hidden_rel = Path("packages/zod/src/v3/tests/public-helper-compat.test.ts")
    hidden = _CORPUS / "evaluator_tests" / "JS4" / hidden_rel

    pristine = _copy_repo(source, tmp_path / "pristine")
    pristine_dest = pristine / hidden_rel
    pristine_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hidden, pristine_dest)
    pristine_test = node_harness.run_tests(
        pristine, test_path=hidden_rel, timeout=1200, install_timeout=1200
    )
    assert pristine_test.ran and not pristine_test.passed

    harmful = _copy_repo(source, tmp_path / "harmful")
    _apply_patch(harmful, _CORPUS / "oracle_patches" / "JS4.patch")
    harmful_build = node_harness.run_build(
        harmful, profile="zshy", selector="zod:tsconfig.build.json", timeout=1200, install_timeout=1200
    )
    assert harmful_build.ran and harmful_build.passed, harmful_build.error_summary
    harmful_dest = harmful / hidden_rel
    harmful_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hidden, harmful_dest)
    harmful_test = node_harness.run_tests(
        harmful, test_path=hidden_rel, timeout=1200, install_timeout=1200
    )
    assert harmful_test.ran and not harmful_test.passed

    safe = _copy_repo(source, tmp_path / "safe")
    _apply_patch(safe, _CORPUS / "correct_fix_patches" / "JS4.patch")
    safe_build = node_harness.run_build(
        safe, profile="zshy", selector="zod:tsconfig.build.json", timeout=1200, install_timeout=1200
    )
    assert safe_build.ran and safe_build.passed, safe_build.error_summary
    safe_dest = safe / hidden_rel
    safe_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hidden, safe_dest)
    safe_test = node_harness.run_tests(safe, test_path=hidden_rel, timeout=1200, install_timeout=1200)
    assert safe_test.ran and safe_test.passed, safe_test.error_summary
