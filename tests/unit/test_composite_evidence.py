"""Slice 5 — CompositeEvidenceProvider, dep-light-safe unit tests.

These run even without yaml/radon installed: they either exercise the degrade paths (by forcing the
heavy imports to fail) or run on an empty repo where every adapter is inert. The composite must import
and run without yaml/radon present (the dep-light CLI path).
"""

from __future__ import annotations

import sys

import pytest

from pebra.adapters import bandit_adapter as ba
from pebra.adapters import git_adapter
from pebra.adapters.composite_evidence import CompositeEvidenceProvider
from pebra.adapters.request_evidence import RequestEvidenceProvider
from pebra.core.models import AssessmentRequest, CandidateAction


def _req(files=("src/auth.py",), patch=None) -> tuple[AssessmentRequest, CandidateAction]:
    action = CandidateAction(
        id="a1", label="l", action_type="edit", expected_files=list(files), proposed_patch=patch
    )
    return AssessmentRequest(task="t", candidate_actions=[action]), action


def test_import_succeeds_without_heavy_deps() -> None:
    assert CompositeEvidenceProvider is not None


def test_empty_repo_equals_request_only(tmp_path) -> None:
    req, action = _req()
    base = RequestEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    comp = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert comp == base  # nothing to analyze -> every adapter inert -> identical to request-only


def test_missing_yaml_ignores_pebra_yml(tmp_path, monkeypatch) -> None:
    (tmp_path / ".pebra.yml").write_text('criticality:\n  "**": C4\n', encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)  # force `import yaml` to fail
    monkeypatch.delitem(sys.modules, "pebra.adapters.yaml_config", raising=False)
    req, action = _req()
    comp = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert comp.criticality_stage == "C2"  # yaml absent -> config not loaded -> not raised to C4


def test_missing_radon_degrades_to_projected(tmp_path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    for mod in ("radon", "radon.complexity", "radon.metrics"):
        monkeypatch.setitem(sys.modules, mod, None)
    monkeypatch.delitem(sys.modules, "pebra.adapters.radon_adapter", raising=False)
    req, action = _req(patch="--- m\n+++ m\n")
    comp = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert comp.benefit_delta_evidence.source_type == "projected"


def test_current_head_threaded_into_architecture_provenance(tmp_path, monkeypatch) -> None:
    # 5b: the repo HEAD is recorded as graph_commit provenance (freshness stays content-hash based).
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(git_adapter, "head_commit", lambda root: "deadbeef")
    req, action = _req(["m.py"])
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert ev.architecture_evidence.graph_commit == "deadbeef"


def test_no_git_head_leaves_provenance_none(tmp_path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(git_adapter, "head_commit", lambda root: None)
    req, action = _req(["m.py"])
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert ev.architecture_evidence.graph_commit is None  # no HEAD != UNKNOWN (content-hash freshness)


def test_internal_import_bug_is_not_masked_as_degradation(tmp_path, monkeypatch) -> None:
    # an internal import failure (not the external yaml package) must surface, not silently degrade.
    monkeypatch.setitem(sys.modules, "pebra.adapters.yaml_config", None)
    req, action = _req()
    with pytest.raises(ImportError):
        CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))


def test_internal_radon_import_bug_is_not_masked(tmp_path, monkeypatch) -> None:
    # a broken radon_adapter import (not the external radon package) must surface, not degrade.
    monkeypatch.setitem(sys.modules, "pebra.adapters.radon_adapter", None)
    req, action = _req()
    with pytest.raises(ImportError):
        CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))


def test_bandit_unavailable_not_penalized_when_strict_false(tmp_path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("import os\n", encoding="utf-8")
    monkeypatch.setattr(ba, "_run_bandit", lambda py, repo_root: None)  # bandit "cannot run"
    req, action = _req()
    base = RequestEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    comp = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    # default (non-strict): an unavailable tool is inert -> evidence_quality untouched
    assert comp.edit_confidence_factors["evidence_quality"] == base.edit_confidence_factors[
        "evidence_quality"
    ]
