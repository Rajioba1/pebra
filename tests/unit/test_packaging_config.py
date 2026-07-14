from __future__ import annotations

from pathlib import Path
import tomllib


def test_setuptools_discovery_prunes_non_distribution_trees() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    discovery = config["tool"]["setuptools"]["packages"]["find"]

    assert discovery["include"] == ["pebra*"]
    assert set(discovery["exclude"]) >= {"benchmarks*", "docs*", "e2e*", "tests*"}
