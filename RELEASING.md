# Releasing PEBRA

This runbook covers the first public `0.1.x` releases. A release is built once in GitHub Actions and
published from that exact artifact through PyPI Trusted Publishing. Do not upload a locally-built
wheel to PyPI.

## Maintainer Authorization Gate

Do not create or push a release tag, dispatch or rerun the release workflow, approve a publishing
environment, upload to TestPyPI or PyPI, or create a GitHub release without explicit maintainer
authorization for that specific release. Passing tests, a clean release candidate, or approval of an
implementation milestone is not release authorization.

## One-Time Repository Setup

1. Make the repository public only after a manually dispatched or scheduled full-history secret scan
   is clean and any discovered credential has been rotated. Push and pull-request scans cover their
   event ranges; they do not replace the full-history gate.
2. Enable GitHub private vulnerability reporting.
3. Enable GitHub release immutability so published tags and release assets cannot be replaced.
4. Create protected GitHub environments named `testpypi` and `pypi`; require a reviewer for `pypi`.
5. Configure Trusted Publishers on TestPyPI and PyPI with:
   - owner: `Rajioba1`;
   - repository: `pebra`;
   - workflow: `release.yml`;
   - environment: `testpypi` or `pypi`, respectively.
6. Protect `main`, require CI and secret-scan checks, and require GitHub Actions to use full-length
   commit SHAs.

The release workflow requests short-lived OpenID Connect credentials. Do not add a long-lived PyPI
API token to repository or environment secrets.

## Prepare A Release

1. Update `project.version` in `pyproject.toml` and write the GitHub release notes. Never reuse a
   version that has been uploaded to PyPI or TestPyPI.
2. Confirm the working tree is clean and `main` is synchronized with `origin/main`.
3. Run the release checks:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q
   .\.venv\Scripts\ruff.exe check .
   .\.venv\Scripts\lint-imports.exe
   .\.venv\Scripts\python.exe -m build
   .\.venv\Scripts\twine.exe check dist\*
   .\.venv\Scripts\python.exe scripts\verify_distribution.py archives dist
   ```

4. Confirm the cross-platform installed-wheel, CodeGraph, RCA-degradation, Playwright, and secret-scan
   jobs are green for the release commit.
5. Create and push an annotated release tag matching `pyproject.toml`, for example `v0.1.1`. Sign
   the tag when a maintainer signing key is available; OIDC publishing and artifact attestations
   remain the authoritative automated provenance controls.

## TestPyPI Gate

Run the `Release` workflow from `main` and supply the annotated tag as `release_tag`. The workflow
verifies that the tag is on `main`, builds the candidate once, records the tag, commit, wheel, and
sdist digests in `CANDIDATE.json`, and publishes those bytes to the protected `testpypi` environment.

Install the uploaded version in a clean environment using TestPyPI plus PyPI for runtime dependencies,
then repeat CLI and dashboard smoke checks. The production `pypi` job cannot start unless TestPyPI
succeeds, and it waits for the protected-environment reviewer. Reject that deployment if the smoke
test fails. If a candidate must change after TestPyPI publication, increment the version and tag;
do not reuse uploaded filenames.

Do not create the production GitHub release manually; the workflow creates it only after TestPyPI
and PyPI succeed.

## Publish

1. Approve the waiting `pypi` environment only after the TestPyPI smoke passes.
2. The production job verifies `CANDIDATE.json`, `SHA256SUMS`, and both distributions before
   publishing the same workflow artifact through PyPI Trusted Publishing. After PyPI succeeds, the
   workflow creates the GitHub release and attaches the wheel, sdist, checksum, and candidate
   manifest. GitHub release immutability then locks the release tag and assets.
3. Download the release assets and verify their hashes before installing from PyPI:

   ```powershell
   gh release download v0.1.1 --repo Rajioba1/pebra --dir release-assets
   python scripts/verify_distribution.py verify-checksums release-assets release-assets/SHA256SUMS
   python -m venv release-smoke
   .\release-smoke\Scripts\python.exe -m pip install pebra==0.1.1
   .\release-smoke\Scripts\python.exe -m pebra --help
   .\release-smoke\Scripts\python.exe -m pebra dashboard --port 0
   ```

Use the platform-equivalent virtual-environment executable paths on macOS and Linux.

## Failed Release Or Rollback

- Stop or reject the protected environment deployment before publishing when a release job fails.
- If PyPI succeeds but `create-github-release` fails, rerun only that failed job in the same workflow
  run while the `release-candidate` artifact is retained. Do not rerun the full publication workflow
  or upload the version again.
- PyPI releases are immutable. Fix the defect and publish a new patch version; never replace files for
  an existing version.
- Yank a broken release on PyPI so new dependency resolution avoids it, while preserving the audit
  record. Explain the reason in the GitHub release and follow-up release notes.
- If a credential is exposed, rotate or revoke it immediately even if history is later rewritten.
- If published source history must be rewritten, coordinate the disruption explicitly and repeat the
  complete history scan before restoring visibility or publishing again.
