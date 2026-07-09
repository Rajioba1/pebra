"""Parity: the observatory's boundary-safe repo_id twin (``launch_dashboard.repo_id_for``)
MUST equal production ``RepositoryRegistry.resolve(...).repo_id``.

The dashboard filters every data route by repo_id (``WHERE a.repo_id = ?``), so a drifted twin would make
the observatory drilldown silently render an EMPTY repo (well-formed SQL, HTTP 200, zero rows) — no error.
The e2e tree may not ``import pebra`` (boundary discipline); this tests/-side pin is what keeps the twin
honest, exactly like the find_rca / find_engine / candidate_patch_hash parity tests.
"""

from __future__ import annotations

from e2e.experiments.agent_ab.runners.launch_dashboard import repo_id_for
from pebra.adapters.repository_registry import RepositoryRegistry


def test_repo_id_twin_matches_production_plain_dir(tmp_path):
    root = str(tmp_path)  # no marker -> find_repo_root falls back to the resolved start dir
    assert repo_id_for(root) == RepositoryRegistry().resolve(root).repo_id


def test_repo_id_twin_matches_production_for_a_git_anchored_clone(tmp_path):
    # Assay clones ARE git repos, so the observatory passes a .git-anchored repo dir to repo_id_for.
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    # production resolve() from a nested path anchors up to the .git root; the twin is given that root.
    assert repo_id_for(str(tmp_path)) == RepositoryRegistry().resolve(str(nested)).repo_id
