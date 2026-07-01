"""Session fixtures for the heavy external lane (gated E2E_EXTERNAL=1).

Indexes the isolated copy ONCE per session; provides a no-index clone for the graph-vs-no-graph delta.
The source repo is never mutated (repo_source clones it into the gitignored e2e/out/).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from e2e.external.utils import compiler_scenario
from e2e.external.utils import dotnet_harness as dn
from e2e.external.utils import repo_source as rs
from e2e.utils import cli_harness as ch

_EXTERNAL = os.environ.get("E2E_EXTERNAL") == "1"


@pytest.fixture(scope="session", autouse=True)
def _require_external():
    if not _EXTERNAL:
        pytest.skip(
            f"set E2E_EXTERNAL=1 and {rs.ENV_VAR}=<local git checkout of template_blueprint>"
        )


@pytest.fixture(scope="session")
def external_repo() -> rs.ExternalRepo:
    return rs.prepare_external_repo()


@pytest.fixture(scope="session")
def indexed_copy(external_repo) -> Path:
    """The isolated copy with a fresh CodeGraph index (built once)."""
    ch.setup_graph(repo_root=external_repo.copy_path)
    return external_repo.copy_path


@pytest.fixture(scope="session")
def nograph_copy(external_repo, tmp_path_factory) -> Path:
    """A clean clone of the SAME source with NO CodeGraph index — the no-graph control arm."""
    dest = tmp_path_factory.mktemp("nograph") / "repo"
    return rs.clone_at_recorded_head(external_repo, dest)


@pytest.fixture(scope="session")
def nograph_env(tmp_path_factory) -> dict[str, str]:
    """Force codegraph to use an empty per-run index dir for the no-graph control arm."""
    empty_index = tmp_path_factory.mktemp("nograph-codegraph-dir")
    return {"CODEGRAPH_DIR": str(empty_index)}


@pytest.fixture(scope="session")
def build_copy(external_repo, tmp_path_factory) -> Path:
    """A dedicated buildable clone (no CodeGraph index needed) for the compiler-outcome lane — kept
    apart from the graph copy so the signature edit + dotnet build never disturb the graph scenario."""
    dest = tmp_path_factory.mktemp("buildcopy") / "repo"
    return rs.clone_at_recorded_head(external_repo, dest)


@pytest.fixture(scope="session")
def compiler_outcome_state(build_copy, tmp_path_factory):
    """Run the full compiler-outcome flow ONCE (real build cycle + seeded history + promote + reassess)."""
    if not dn.dotnet_available():
        pytest.skip("dotnet SDK not found — skipping the compiler-outcome lane")
    db = tmp_path_factory.mktemp("compilerdb") / "pebra.db"
    return compiler_scenario.build_compiler_outcome_state(build_copy, db)
