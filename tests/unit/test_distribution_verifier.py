from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.verify_distribution import (
    DistributionVerificationError,
    release_version_from_tag,
    verify_candidate_manifest,
    verify_checksums,
    verify_archives,
    write_candidate_manifest,
    write_checksums,
)


_ASSETS = (
    "pebra/dashboard/templates/index.html",
    "pebra/dashboard/static/app.js",
    "pebra/dashboard/static/style.css",
    "pebra/dashboard/static/vendor/uplot.iife.min.js",
    "pebra/dashboard/static/vendor/uplot.min.css",
    "pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    "pebra/tui/theme.tcss",
)


def _write_wheel(
    path: Path,
    *,
    omit: str | None = None,
    prefix: str = "",
) -> None:
    members = [
        *_ASSETS,
        "pebra-0.1.0.dist-info/licenses/LICENSE",
        "pebra-0.1.0.dist-info/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    ]
    with zipfile.ZipFile(path, "w") as archive:
        for member in members:
            if member != omit:
                archive.writestr(f"{prefix}{member}", "content")


def _write_sdist(
    path: Path,
    *,
    root: str = "pebra-0.1.0",
    extra_directory: str | None = None,
) -> None:
    members = [
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "RELEASING.md",
        "README.md",
        "pyproject.toml",
        *_ASSETS,
    ]
    staging = path.parent / "staging"
    staging.mkdir()
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            source = staging / member
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("content", encoding="utf-8")
            archive.add(source, arcname=f"{root}/{member}")
        if extra_directory is not None:
            info = tarfile.TarInfo(extra_directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)


def test_archive_verifier_accepts_complete_wheel_and_sdist(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist)

    verify_archives(wheel, sdist)


def test_archive_verifier_names_a_missing_runtime_asset(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    missing = "pebra/dashboard/static/vendor/uplot.LICENSE.txt"
    _write_wheel(wheel, omit=missing)
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="uplot.LICENSE.txt"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_wheel_members_under_wrong_prefix(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel, prefix="wrong-prefix/")
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="missing"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_sdist_members_under_nested_root(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist, root="pebra-0.1.0/wrong-prefix")

    with pytest.raises(DistributionVerificationError, match="missing"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_backslash_wheel_members(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    members = [
        *_ASSETS,
        "pebra-0.1.0.dist-info/licenses/LICENSE",
        "pebra-0.1.0.dist-info/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    ]
    with zipfile.ZipFile(wheel, "w") as archive:
        for member in members:
            archive.writestr(member, "content")
    raw_wheel = wheel.read_bytes()
    for member in members:
        raw_wheel = raw_wheel.replace(
            member.encode("ascii"),
            member.replace("/", "\\").encode("ascii"),
        )
    wheel.write_bytes(raw_wheel)
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="backslash"):
        verify_archives(wheel, sdist)


def test_archive_verifier_requires_uplot_metadata_license(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    missing = "pebra-0.1.0.dist-info/licenses/pebra/dashboard/static/vendor/uplot.LICENSE.txt"
    _write_wheel(wheel, omit=missing)
    _write_sdist(sdist)

    with pytest.raises(DistributionVerificationError, match="uplot.LICENSE.txt"):
        verify_archives(wheel, sdist)


def test_archive_verifier_rejects_backslash_tar_directory(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist, extra_directory="pebra-0.1.0/bad\\directory")

    with pytest.raises(DistributionVerificationError, match="backslash"):
        verify_archives(wheel, sdist)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v0.1.0", "0.1.0"), ("0.1.0", "0.1.0"), ("release-0.1.0", None)],
)
def test_release_version_from_tag_is_strict(tag: str, expected: str | None) -> None:
    assert release_version_from_tag(tag) == expected


def test_installed_verifier_exercises_console_script() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "verify_distribution.py").read_text(
        encoding="utf-8"
    )

    assert "shutil.which(\"pebra\")" in source
    assert "installed console script" in source


def test_checksums_detect_artifact_tampering(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    manifest = write_checksums(tmp_path)

    verify_checksums(tmp_path, manifest)
    wheel.write_bytes(b"tampered")

    with pytest.raises(DistributionVerificationError, match="checksum mismatch"):
        verify_checksums(tmp_path, manifest)


def test_candidate_manifest_binds_tag_commit_and_artifacts(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    commit = "a" * 40
    manifest = write_candidate_manifest(tmp_path, tmp_path / "CANDIDATE.json", "v0.1.0", commit)

    verify_candidate_manifest(tmp_path, manifest, expected_tag="v0.1.0", expected_commit=commit)

    with pytest.raises(DistributionVerificationError, match="candidate tag mismatch"):
        verify_candidate_manifest(tmp_path, manifest, expected_tag="v0.1.1", expected_commit=commit)
    with pytest.raises(DistributionVerificationError, match="candidate commit mismatch"):
        verify_candidate_manifest(
            tmp_path,
            manifest,
            expected_tag="v0.1.0",
            expected_commit="b" * 40,
        )

    wheel.write_bytes(b"tampered")
    with pytest.raises(DistributionVerificationError, match="candidate artifact mismatch"):
        verify_candidate_manifest(tmp_path, manifest, expected_tag="v0.1.0", expected_commit=commit)
