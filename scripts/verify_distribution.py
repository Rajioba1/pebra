"""Validate PEBRA release archives and an installed wheel using only public package surfaces."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.metadata
import importlib.resources
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Sequence


_PACKAGE_ASSETS = (
    "pebra/dashboard/templates/index.html",
    "pebra/dashboard/static/app.js",
    "pebra/dashboard/static/style.css",
    "pebra/dashboard/static/vendor/uplot.iife.min.js",
    "pebra/dashboard/static/vendor/uplot.min.css",
    "pebra/dashboard/static/vendor/uplot.LICENSE.txt",
)
_SDIST_ROOT_FILES = (
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "DEVELOPMENT.md",
    "RELEASING.md",
    "README.md",
    "pyproject.toml",
)
_TAG = re.compile(r"^v?(\d+\.\d+\.\d+(?:[a-zA-Z0-9.-]+)?)$")
_CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^/\\]+)$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class DistributionVerificationError(RuntimeError):
    """A built or installed distribution does not satisfy the release contract."""


def _missing_exact(members: set[str], required: Sequence[str]) -> list[str]:
    return [item for item in required if item not in members]


def verify_archives(wheel: Path, sdist: Path) -> None:
    """Require runtime assets and legal/release documents in the built archives."""
    with zipfile.ZipFile(wheel) as archive:
        wheel_info = archive.infolist()
    if any("\\" in member.orig_filename for member in wheel_info):
        raise DistributionVerificationError("wheel contains a non-portable backslash member")
    wheel_members = {member.filename for member in wheel_info}
    wheel_parts = wheel.name.removesuffix(".whl").split("-")
    if len(wheel_parts) < 2:
        raise DistributionVerificationError(f"invalid wheel filename: {wheel.name}")
    dist_info = f"{wheel_parts[0]}-{wheel_parts[1]}.dist-info"
    wheel_required = (
        *_PACKAGE_ASSETS,
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    )
    missing_wheel = _missing_exact(wheel_members, wheel_required)

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_info = archive.getmembers()
    if any("\\" in member.name for member in sdist_info):
        raise DistributionVerificationError("sdist contains a non-portable backslash member")
    sdist_members = {member.name for member in sdist_info if member.isfile()}
    sdist_root = sdist.name.removesuffix(".tar.gz")
    sdist_required = {
        f"{sdist_root}/{relative}" for relative in (*_SDIST_ROOT_FILES, *_PACKAGE_ASSETS)
    }
    missing_sdist = _missing_exact(sdist_members, sorted(sdist_required))

    problems = []
    if missing_wheel:
        problems.append("wheel missing: " + ", ".join(missing_wheel))
    if missing_sdist:
        problems.append("sdist missing: " + ", ".join(missing_sdist))
    if problems:
        raise DistributionVerificationError("; ".join(problems))


def release_version_from_tag(tag: str) -> str | None:
    match = _TAG.fullmatch(tag)
    return match.group(1) if match else None


def _run_cli(*args: str, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "pebra", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def verify_installed() -> None:
    """Check the installed wheel without importing PEBRA from its source checkout."""
    dashboard = importlib.resources.files("pebra.dashboard")
    for relative in (
        "templates/index.html",
        "static/app.js",
        "static/style.css",
        "static/vendor/uplot.iife.min.js",
        "static/vendor/uplot.min.css",
        "static/vendor/uplot.LICENSE.txt",
    ):
        if not dashboard.joinpath(relative).is_file():
            raise DistributionVerificationError(f"installed package missing {relative}")

    metadata_files = {str(path).replace("\\", "/") for path in importlib.metadata.files("pebra") or ()}
    for suffix in ("licenses/LICENSE", "licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt"):
        if not any(path.endswith(suffix) for path in metadata_files):
            raise DistributionVerificationError(f"installed metadata missing {suffix}")

    with tempfile.TemporaryDirectory(prefix="pebra-wheel-smoke-") as raw:
        cwd = Path(raw)
        for args in (("--help",), ("help", "dashboard")):
            result = _run_cli(*args, cwd=cwd)
            if result.returncode != 0 or "pebra" not in result.stdout.lower():
                raise DistributionVerificationError(
                    f"installed CLI failed for {' '.join(args)}: {result.stderr.strip()}"
                )
        launcher = shutil.which("pebra")
        if launcher is None:
            raise DistributionVerificationError("installed console script was not found on PATH")
        console = subprocess.run(
            [launcher, "--help"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
        if console.returncode != 0 or "pebra" not in console.stdout.lower():
            raise DistributionVerificationError(
                f"installed console script failed: {console.stderr.strip()}"
            )

    old_path = os.environ.get("PATH")
    old_override = os.environ.get("PEBRA_RCA_BIN")
    try:
        os.environ["PATH"] = ""
        os.environ["PEBRA_RCA_BIN"] = str(Path.cwd() / "missing-rca")
        from pebra.adapters.rca_adapter import RustCodeAnalysisAdapter

        if RustCodeAnalysisAdapter().measure_delta("sample.py", "x = 1\n", "x = 2\n") is not None:
            raise DistributionVerificationError("missing RCA engine did not degrade safely")
    finally:
        if old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old_path
        if old_override is None:
            os.environ.pop("PEBRA_RCA_BIN", None)
        else:
            os.environ["PEBRA_RCA_BIN"] = old_override


def verify_codegraph_setup() -> None:
    """Install/index the pinned CodeGraph release against a tiny repository, then run doctor."""
    with tempfile.TemporaryDirectory(prefix="pebra-codegraph-smoke-") as raw:
        repo = Path(raw)
        (repo / "sample.ts").write_text(
            "export function add(left: number, right: number): number { return left + right; }\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
        setup = _run_cli(
            "setup-graph", "--fix", "--via", "standalone", "--repo-root", str(repo), "--json",
            cwd=repo,
            timeout=900,
        )
        if setup.returncode != 0:
            raise DistributionVerificationError(
                f"CodeGraph setup failed: {(setup.stderr or setup.stdout).strip()}"
            )
        doctor = _run_cli("doctor", "--repo-root", str(repo), "--json", cwd=repo)
        try:
            payload = json.loads(doctor.stdout)
        except json.JSONDecodeError as exc:
            raise DistributionVerificationError("doctor did not emit JSON") from exc
        if doctor.returncode != 0 or payload.get("ok") is not True:
            raise DistributionVerificationError(f"CodeGraph doctor failed: {payload}")


def _distribution_artifacts(dist_dir: Path) -> list[Path]:
    return sorted(
        path for path in dist_dir.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )


def write_checksums(dist_dir: Path) -> Path:
    artifacts = _distribution_artifacts(dist_dir)
    if not artifacts:
        raise DistributionVerificationError(f"no distributions found under {dist_dir}")
    output = dist_dir / "SHA256SUMS"
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in artifacts]
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return output


def _artifact_digests(dist_dir: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in _distribution_artifacts(dist_dir)
    }


def write_candidate_manifest(dist_dir: Path, output: Path, tag: str, commit: str) -> Path:
    """Bind one candidate's tag and source commit to its exact distribution bytes."""
    if release_version_from_tag(tag) is None:
        raise DistributionVerificationError(f"invalid candidate tag: {tag!r}")
    if _COMMIT.fullmatch(commit) is None:
        raise DistributionVerificationError(f"invalid candidate commit: {commit!r}")
    artifacts = _artifact_digests(dist_dir)
    if not artifacts:
        raise DistributionVerificationError(f"no distributions found under {dist_dir}")
    payload = {
        "schema": "pebra.release-candidate.v1",
        "tag": tag,
        "commit": commit,
        "artifacts": artifacts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def verify_candidate_manifest(
    dist_dir: Path,
    manifest: Path,
    *,
    expected_tag: str,
    expected_commit: str,
) -> None:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DistributionVerificationError("candidate manifest is not valid JSON") from exc
    if payload.get("schema") != "pebra.release-candidate.v1":
        raise DistributionVerificationError("candidate manifest schema mismatch")
    if payload.get("tag") != expected_tag:
        raise DistributionVerificationError("candidate tag mismatch")
    if payload.get("commit") != expected_commit:
        raise DistributionVerificationError("candidate commit mismatch")
    if payload.get("artifacts") != _artifact_digests(dist_dir):
        raise DistributionVerificationError("candidate artifact mismatch")


def verify_checksums(dist_dir: Path, manifest: Path) -> None:
    """Verify that the manifest covers exactly the wheel and sdist in ``dist_dir``."""
    expected: dict[str, str] = {}
    for line in manifest.read_text(encoding="ascii").splitlines():
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise DistributionVerificationError(f"invalid checksum line: {line!r}")
        digest, name = match.groups()
        if name in expected:
            raise DistributionVerificationError(f"duplicate checksum entry: {name}")
        expected[name] = digest

    artifacts = {
        path.name: path
        for path in dist_dir.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    }
    if set(expected) != set(artifacts):
        raise DistributionVerificationError(
            "checksum manifest does not exactly match distributions: "
            f"manifest={sorted(expected)}, artifacts={sorted(artifacts)}"
        )
    for name, path in artifacts.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if not hmac.compare_digest(expected[name], actual):
            raise DistributionVerificationError(f"checksum mismatch: {name}")


def _one(dist_dir: Path, pattern: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise DistributionVerificationError(
            f"expected exactly one {pattern} under {dist_dir}, found {len(matches)}"
        )
    return matches[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    archives = subparsers.add_parser("archives")
    archives.add_argument("dist_dir", type=Path)
    subparsers.add_parser("installed")
    subparsers.add_parser("codegraph")
    checksums = subparsers.add_parser("checksums")
    checksums.add_argument("dist_dir", type=Path)
    verify_manifest = subparsers.add_parser("verify-checksums")
    verify_manifest.add_argument("dist_dir", type=Path)
    verify_manifest.add_argument("manifest", type=Path)
    candidate = subparsers.add_parser("candidate-manifest")
    candidate.add_argument("dist_dir", type=Path)
    candidate.add_argument("output", type=Path)
    candidate.add_argument("--tag", required=True)
    candidate.add_argument("--commit", required=True)
    verify_candidate = subparsers.add_parser("verify-candidate")
    verify_candidate.add_argument("dist_dir", type=Path)
    verify_candidate.add_argument("manifest", type=Path)
    verify_candidate.add_argument("--tag", required=True)
    verify_candidate.add_argument("--commit", required=True)
    release_tag = subparsers.add_parser("release-tag")
    release_tag.add_argument("tag")
    release_tag.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "archives":
            verify_archives(_one(args.dist_dir, "*.whl"), _one(args.dist_dir, "*.tar.gz"))
        elif args.command == "installed":
            verify_installed()
        elif args.command == "codegraph":
            verify_codegraph_setup()
        elif args.command == "checksums":
            print(write_checksums(args.dist_dir))
        elif args.command == "verify-checksums":
            verify_checksums(args.dist_dir, args.manifest)
        elif args.command == "candidate-manifest":
            print(write_candidate_manifest(args.dist_dir, args.output, args.tag, args.commit))
        elif args.command == "verify-candidate":
            verify_candidate_manifest(
                args.dist_dir,
                args.manifest,
                expected_tag=args.tag,
                expected_commit=args.commit,
            )
        else:
            tag_version = release_version_from_tag(args.tag)
            project = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]
            if tag_version is None or tag_version != project["version"]:
                raise DistributionVerificationError(
                    f"release tag {args.tag!r} does not match project version {project['version']!r}"
                )
    except (DistributionVerificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"distribution verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"distribution verification passed: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
