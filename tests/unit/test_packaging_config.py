from __future__ import annotations

import ast
from pathlib import Path
import tomllib


def test_setuptools_discovery_prunes_non_distribution_trees() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    discovery = config["tool"]["setuptools"]["packages"]["find"]

    assert discovery["include"] == ["pebra*"]
    assert set(discovery["exclude"]) >= {"benchmarks*", "docs*", "e2e*", "tests*"}
    assert discovery["namespaces"] is False


def test_dashboard_runtime_assets_are_explicit_package_data() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["setuptools"]["package-data"]["pebra.dashboard"] == [
        "templates/*.html",
        "static/*.js",
        "static/*.css",
        "static/vendor/*.js",
        "static/vendor/*.css",
        "static/vendor/*.txt",
    ]


def test_tui_theme_asset_is_explicit_package_data() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["setuptools"]["package-data"]["pebra.tui"] == ["*.tcss"]


def test_textual_is_a_pinned_runtime_dependency() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = [d.replace(" ", "") for d in config["project"]["dependencies"]]
    assert "textual>=8.2,<9" in deps, deps


def test_nox_tests_use_the_supported_textual_range() -> None:
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "noxfile.py").read_text(encoding="utf-8"))
    dev_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DEV" for target in node.targets)
    )
    dev = ast.literal_eval(dev_assignment.value)

    assert "textual>=8.2,<9" in dev
    assert "textual" not in dev


def test_snapshot_plugin_is_exactly_pinned_in_dev_environments() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project_dev = config["dependency-groups"]["dev"]
    tree = ast.parse((root / "noxfile.py").read_text(encoding="utf-8"))
    dev_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DEV" for target in node.targets)
    )
    nox_dev = ast.literal_eval(dev_assignment.value)

    assert "pytest-textual-snapshot==1.1.0" in project_dev
    assert "pytest-textual-snapshot==1.1.0" in nox_dev


def test_tui_contributor_setup_installs_devtools_and_uses_project_venv() -> None:
    root = Path(__file__).resolve().parents[2]
    guide = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "textual-dev pytest-textual-snapshot==1.1.0" in guide
    assert (
        r".\.venv\Scripts\python.exe -m pytest tests\snapshots --snapshot-update" in guide
    )
    assert "`pytest tests/snapshots --snapshot-update`" not in guide


def test_source_distribution_manifest_includes_release_documents() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()

    assert manifest == [
        "include LICENSE",
        "include SECURITY.md",
        "include CONTRIBUTING.md",
        "include RELEASING.md",
        "include requirements-release.in",
        "include requirements-release.txt",
        "include README.md",
        "recursive-include pebra/dashboard/templates *.html",
        "recursive-include pebra/dashboard/static *.js *.css *.txt",
        "recursive-include pebra/tui *.tcss",
    ]


def test_release_frontend_versions_are_pinned() -> None:
    root = Path(__file__).resolve().parents[2]
    direct = (root / "requirements-release.in").read_text(encoding="utf-8").splitlines()
    lock = (root / "requirements-release.txt").read_text(encoding="utf-8")

    assert direct == [
        "build==1.4.0",
        "setuptools==83.0.0",
        "twine==6.2.0",
        "wheel==0.47.0",
    ]
    for requirement in direct:
        assert requirement in lock
    assert "--hash=sha256:" in lock


def test_public_documents_do_not_link_to_private_development_runbook() -> None:
    root = Path(__file__).resolve().parents[2]

    for name in ("README.md", "CONTRIBUTING.md"):
        assert "DEVELOPMENT.md" not in (root / name).read_text(encoding="utf-8")
