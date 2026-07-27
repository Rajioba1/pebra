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
    body = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    policy = body.lower()

    assert "supported versions" in policy
    assert "private vulnerability reporting" in policy
    assert "do not open a public issue" in policy
    assert "business days" in policy
    assert "Latest verified PyPI release" in body
    assert "Development branch: `main`" in body
    assert "only after its PyPI publication is verified" in body


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
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == [
        "LICENSE",
        "THIRD_PARTY_LICENSES.txt",
        "pebra/dashboard/static/vendor/uplot.LICENSE.txt",
        "pebra/dashboard/static/vendor/cytoscape.LICENSE.txt",
    ]

    classifiers = set(project["classifiers"])
    assert {
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Quality Assurance",
    } <= classifiers
    assert not any(value.startswith("Development Status :: 3 - Alpha") for value in classifiers)
    assert not any(value.startswith("License ::") for value in classifiers)


def test_third_party_notices_cover_bundled_dashboard_assets() -> None:
    body = (ROOT / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")

    assert "uPlot" in body
    assert "Cytoscape.js" in body
    assert "MIT License" in body
    assert "pebra/dashboard/static/vendor/uplot.LICENSE.txt" in body
    assert "pebra/dashboard/static/vendor/cytoscape.LICENSE.txt" in body


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
        "pebra update",
        "pebra help tui",
        "pebra help --all",
    ):
        assert command in body


def test_readme_installs_codegraph_and_pebra_before_workflow() -> None:
    body = (ROOT / "README.md").read_text(encoding="utf-8")
    lowered = body.lower()

    installation = body.index("## Installation")
    product_model = body.index("## Product Model")
    workflow = body.index("## Basic workflow")
    assert installation < product_model < workflow
    installation_body = body[installation:product_model]

    for command in (
        "python -m pip install pebra",
        "pipx install pebra",
        "pebra setup-graph --fix --repo-root .",
        "pebra doctor --repo-root .",
        "pebra agent-init --target claude --repo-root . --with-hook",
        "pebra agent-init --target auto --repo-root . --check --json",
    ):
        assert command in body

    assert "CodeGraph is PEBRA's standard structural engine" in body
    assert "codegraph is optional" not in lowered
    assert "fresh/required" not in lowered
    assert "available or required" not in lowered
    assert "examples/" not in installation_body
    assert "## Quickstart" not in body
    assert "## Best practices" not in body
    assert "## Install & engines" not in body


def test_project_version_is_0_4_1_release_candidate() -> None:
    _, project = _project_metadata()

    assert project["version"] == "0.4.1"
