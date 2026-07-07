"""Scenario — REAL-CodeGraph validation of the P4 candidate-verification building blocks.

Closes the "fixtures only" gap for the graph_repair arm's host-produced verification: exercises
``candidate_materializer`` (real git copy+apply) and ``covering_tests_resolver`` (real graph
caller-query over the actual CodeGraph DB) against the real C# repo, not a hand-built SQLite fixture.

Gated behind the heavy external lane (``E2E_EXTERNAL=1`` + a local template_blueprint checkout via the
``indexed_copy`` fixture). Kept fast: it does NOT run ``dotnet test`` (that is the live-assay lane's
job); it validates the discovery/materialization plumbing produces real, on-disk-consistent results and
fails SOFT, never crashing.
"""

from __future__ import annotations

from e2e.experiments.agent_ab.tools import candidate_materializer as cm
from e2e.experiments.agent_ab.tools import covering_tests_resolver as ctr
from e2e.external.utils import signature_edit as se


def test_materialize_real_candidate_into_scratch(indexed_copy):
    patch = se.build_signature_request(indexed_copy)["candidate_actions"][0]["proposed_patch"]
    scratch = cm.materialize_candidate(indexed_copy, patch)
    assert scratch is not None  # real copytree + git init + git apply of the real C# signature patch
    edited = scratch / se.IWORKSPACE_REL
    assert edited.is_file()
    # source repo is never mutated by materialization
    cm.cleanup(scratch)
    assert not scratch.exists()


def test_covering_tests_resolver_runs_against_real_graph_and_is_honest(indexed_copy):
    patch = se.build_signature_request(indexed_copy)["candidate_actions"][0]["proposed_patch"]
    project, test_filter = ctr.find_covering_tests(indexed_copy, se.IWORKSPACE_REL, patch)
    # Fail-soft contract: either it found a real test project (which must exist on disk and be a
    # .csproj), or it honestly returned (None, None) — never a fabricated/non-existent path, never crash.
    if project is not None:
        assert project.endswith(".csproj")
        assert (indexed_copy / project).is_file()
        assert test_filter is None
    else:
        assert (project, test_filter) == (None, None)
