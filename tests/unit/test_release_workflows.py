from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
_PINNED_ACTION = re.compile(
    r"^\s*(?:-\s+)?uses:\s*[^\s@]+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE
)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_every_external_action_is_pinned_to_an_immutable_commit() -> None:
    workflows = [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
    assert {path.name for path in workflows} == {"ci.yml", "release.yml", "security.yml"}

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        uses_lines = [
            line
            for line in text.splitlines()
            if line.lstrip().startswith(("- uses:", "uses:"))
        ]
        assert uses_lines, path
        assert len(_PINNED_ACTION.findall(text)) == len(uses_lines), (path, uses_lines)
        assert "pull_request_target" not in text


def test_ci_builds_once_and_tests_installed_wheel_on_all_supported_platforms() -> None:
    workflow = _workflow("ci.yml")

    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "macos-latest" in workflow
    assert "python -m build" in workflow
    assert "python -m twine check" in workflow
    assert "verify_distribution.py archives" in workflow
    assert "verify_distribution.py installed" in workflow
    assert "verify_distribution.py codegraph" in workflow
    assert "pip check" in workflow
    assert "--no-deps" not in workflow
    assert "nox -s e2e-ui" in workflow
    assert "E2E_UI_INSTALL_DEPS: \"1\"" in workflow
    assert "E2E_UI_SKIP_BROWSER_INSTALL" not in workflow
    assert "e2e-ui.log" in workflow
    assert "if: always()" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_security_workflow_has_recurring_full_history_scan_and_no_write_permission() -> None:
    workflow = _workflow("security.yml")

    assert "fetch-depth: 0" in workflow
    assert "gitleaks/gitleaks-action" in workflow
    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "pull-requests: read" in workflow
    assert "id-token: write" not in workflow


def test_release_uses_trusted_publishing_checksums_and_attestation() -> None:
    workflow = _workflow("release.yml")

    assert "release:\n    types: [published]" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "release_tag:" in workflow
    assert "candidate_run_id:" in workflow
    assert "verify_distribution.py release-tag" in workflow
    assert "verify_distribution.py checksums" in workflow
    assert "verify_distribution.py verify-checksums" in workflow
    assert "verify_distribution.py candidate-manifest" in workflow
    assert "verify_distribution.py verify-candidate" in workflow
    assert 'git merge-base --is-ancestor "$CANDIDATE_SHA" origin/main' in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "needs: [build-candidate, publish-testpypi]" in workflow
    assert "run-id: ${{ inputs.candidate_run_id }}" in workflow
    assert "verify_distribution.py index-digests" in workflow
    assert "inputs.candidate_run_id == ''" in workflow
    assert "needs.publish-testpypi.result == 'skipped'" in workflow
    assert "ref: ${{ needs.build-candidate.outputs.candidate-sha }}" in workflow
    assert workflow.count("refs/tags/release-check^{commit}") == 2
    assert "release already exists" in workflow
    assert "gh release create" in workflow
    assert "--clobber" not in workflow
    assert "gh release download" not in workflow
    assert "requirements-release.txt" in workflow
    assert "--require-hashes" in workflow
    assert "python -m build --no-isolation" in workflow
    assert workflow.count('GH_VERSION: "2.96.0"') == 2
    assert workflow.count(
        "GH_SHA256: 83d5c2ccad5498f58bf6368acb1ab32588cf43ab3a4b1c301bf36328b1c8bd60"
    ) == 2
    assert "actions/attest" in workflow
    assert "pypa/gh-action-pypi-publish" in workflow
    assert "environment: testpypi" in workflow
    assert "environment: pypi" in workflow
    assert "repository-url: https://test.pypi.org/legacy/" in workflow
    assert workflow.count("id-token: write") == 3
    publish_pypi = workflow.split("  publish-pypi:", 1)[1].split("  create-github-release:", 1)[0]
    assert "contents: read" in publish_pypi
    assert "password:" not in workflow
