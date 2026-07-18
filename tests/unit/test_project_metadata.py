from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _project_metadata() -> tuple[dict[str, object], dict[str, object]]:
    parsed = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return parsed["build-system"], parsed["project"]


def test_repository_contains_canonical_apache_2_license() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "http://www.apache.org/licenses/" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text


def test_security_policy_defines_private_reporting_and_response_targets() -> None:
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8").lower()

    assert "supported versions" in policy
    assert "private vulnerability reporting" in policy
    assert "do not open a public issue" in policy
    assert "business days" in policy


def test_contribution_terms_cover_rights_license_and_private_security_reports() -> None:
    guide = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").lower()

    assert "apache license 2.0" in guide
    assert "right to submit" in guide
    assert "commercial use" in guide
    assert "security.md" in guide
    assert "do not open a public issue" in guide


def test_pyproject_uses_current_spdx_license_metadata() -> None:
    build_system, project = _project_metadata()

    assert build_system["requires"] == ["setuptools==83.0.0", "wheel==0.47.0"]
    assert project["authors"] == [{"name": "PEBRA contributors"}]
    assert project["license"] == "Apache-2.0 AND MIT"
    assert project["license-files"] == [
        "LICENSE",
        "pebra/dashboard/static/vendor/uplot.LICENSE.txt",
    ]

    classifiers = set(project["classifiers"])
    assert {
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Quality Assurance",
    } <= classifiers
    assert not any(value.startswith("License ::") for value in classifiers)


def test_pyproject_points_to_the_public_github_surfaces() -> None:
    _, project = _project_metadata()

    assert project["urls"] == {
        "Homepage": "https://github.com/Rajioba1/pebra",
        "Repository": "https://github.com/Rajioba1/pebra",
        "Issues": "https://github.com/Rajioba1/pebra/issues",
        "Releases": "https://github.com/Rajioba1/pebra/releases",
    }


def test_readme_documents_cli_and_tui_discovery_commands() -> None:
    body = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in (
        "pebra tui --repo-root .",
        r".\.venv\Scripts\python.exe -m pebra tui --repo-root .",
        "pebra --version",
        "pebra --help",
        "pebra help tui",
        "pebra help --all",
    ):
        assert command in body
