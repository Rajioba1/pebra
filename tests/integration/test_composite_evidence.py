"""Slice 5 — CompositeEvidenceProvider integration + live gate-12 (needs yaml/radon/bandit -> nox).

Calls the composite directly on a tmp repo (cleaner than spawning the CLI), and drives the full
controller+engine for the architecture-map gate (gate 12). Skipped in the dep-light env.
"""

from __future__ import annotations

import difflib
import importlib.util

import pytest

from pebra.adapters import bandit_adapter as ba
from pebra.adapters import import_graph_cache as igc
from pebra.adapters.composite_evidence import CompositeEvidenceProvider
from pebra.adapters.request_evidence import RequestEvidenceProvider
from pebra.app import assess_controller as ac
from pebra.core.constants import Decision
from pebra.core.models import (
    AssessmentRequest,
    BlastEvidence,
    CandidateAction,
    SymbolDiffEvidence,
)
from pebra.ports.repository_registry_port import RepoMetadata

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("yaml", "radon", "bandit")),
    reason="requires yaml/radon/bandit (run via nox)",
)

_THRESHOLDS = {
    "max_expected_loss_without_human": 0.45,
    "c3_max_expected_loss_without_human": 0.20,
    "max_utility_sd_without_human": 0.20,
    "high_edit_confidence": 0.75,
    "low_edit_confidence": 0.50,
    "rau_bands": {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40},
}


def _req(files, patch=None, evidence=None) -> tuple[AssessmentRequest, CandidateAction]:
    action = CandidateAction(
        id="a1", label="l", action_type="edit", expected_files=list(files), proposed_patch=patch
    )
    return (
        AssessmentRequest(
            task="t", candidate_actions=[action], evidence=evidence or {}, thresholds=_THRESHOLDS
        ),
        action,
    )


# --- composite merge behavior (real adapters) ---

def test_yaml_config_raises_criticality_for_payment_path(tmp_path) -> None:
    (tmp_path / ".pebra.yml").write_text('criticality:\n  "payments/**": C4\n', encoding="utf-8")
    (tmp_path / "payments").mkdir()
    (tmp_path / "payments" / "charge.py").write_text("x = 1\n", encoding="utf-8")
    req, action = _req(["payments/charge.py"])
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert ev.criticality_stage == "C4"


def test_bandit_eval_produces_security_event(tmp_path) -> None:
    (tmp_path / "evil.py").write_text("def r(s):\n    return eval(s)\n", encoding="utf-8")
    req, action = _req(["evil.py"])
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert any(e["event"] == "security_sensitive_change" for e in ev.events)


def test_radon_patch_changes_benefit_deltas(tmp_path) -> None:
    before = "def f(x):\n    return x + 1\n"
    after = "def f(x):\n    if x > 0:\n        for i in range(x):\n            if i:\n                x += i\n    return x + 1\n"
    (tmp_path / "m.py").write_text(before, encoding="utf-8")
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile="m.py", tofile="m.py",
        )
    )
    req, action = _req(["m.py"], patch=patch)
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert ev.benefit_delta_evidence.source_type == "measured"
    assert "complexity_delta" in ev.benefit_delta_evidence.deltas


def test_traversal_expected_file_is_not_scanned(tmp_path) -> None:
    # an escaping path must never be scanned/probed by the composite -> no security event.
    (tmp_path.parent / "secret_eval.py").write_text("def r(s):\n    return eval(s)\n", encoding="utf-8")
    req, action = _req(["../secret_eval.py"])
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert not any(e["event"] == "security_sensitive_change" for e in ev.events)


def test_strict_mode_penalizes_unavailable_bandit(tmp_path, monkeypatch) -> None:
    (tmp_path / ".pebra.yml").write_text("strict_mode: true\n", encoding="utf-8")
    (tmp_path / "m.py").write_text("import os\n", encoding="utf-8")
    monkeypatch.setattr(ba, "_run_bandit", lambda py, repo_root: None)  # bandit cannot run
    req, action = _req(["m.py"])
    base = RequestEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert ev.edit_confidence_factors["evidence_quality"] < base.edit_confidence_factors[
        "evidence_quality"
    ]


def test_no_repo_evidence_equals_request_with_deps_present(tmp_path) -> None:
    req, action = _req(["src/auth.py"])
    base = RequestEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    ev = CompositeEvidenceProvider().gather_evidence(req, action, str(tmp_path))
    assert ev == base  # deps installed but nothing to analyze -> still inert


# --- live architecture-map gate (gate 12) through the full controller ---

class _FakeSymbolDiff:
    def symbol_diff(self, action, repo_root):
        return SymbolDiffEvidence(
            parsed_patch_available=True, changed_symbols=["m.py::f"],
            max_change_kind="BEHAVIORAL", visibility="internal",
            symbol_fan_in_percentile=0.1, consequential_symbol_changed=False,
        )


class _FakeBlast:
    def blast(self, action, repo_root):
        return BlastEvidence(direct_count=0, transitive_count=0)


class _FakeSanction:
    def active_sanction(self, repo_id, action):
        return None

    def create_sanction(self, repo_id, sanction):
        return "sx_1"


class _FakeRegistry:
    def __init__(self, repo_root):
        self._root = repo_root

    def resolve(self, start_path):
        return RepoMetadata(repo_id="r", repo_root=self._root)


class _FakeStore:
    def persist_assessment(self, result, request_payload):
        return "asm_1"

    def validate_chain(self):
        return True


_PROCEED_EVIDENCE = {
    "events": [],
    "p_success": 0.9,
    "immediate_benefit": 0.8,
    "review_cost": 0.1,
    "criticality_stage": "C1",
    "edit_confidence_factors": {
        "p_success": 0.9, "evidence_quality": 0.9, "testability": 0.9,
        "reversibility": 0.9, "source_reliability": 0.9, "scope_control": 0.9,
    },
}
_HIGH_RISK_EVIDENCE = {
    "events": [{"event": "public_api_break", "p_event": 0.6, "elicited_disutility": 0.9}],
    "p_success": 0.5,
    "immediate_benefit": 0.3,
    "review_cost": 0.2,
    "criticality_stage": "C2",
    "edit_confidence_factors": {f: 0.7 for f in (
        "p_success", "evidence_quality", "testability", "reversibility",
        "source_reliability", "scope_control",
    )},
}


def _decide(tmp_path, evidence):
    req, _ = _req(["m.py"], evidence=evidence)
    return ac.assess(
        req, thresholds=_THRESHOLDS, start_path=str(tmp_path),
        evidence_provider=CompositeEvidenceProvider(),
        symbol_diff_provider=_FakeSymbolDiff(),
        blast_provider=_FakeBlast(),
        sanction_port=_FakeSanction(),
        repository_registry=_FakeRegistry(str(tmp_path)),
        store=_FakeStore(),
    ).recommended_result


def test_gate12_fresh_graph_does_not_fire(tmp_path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    assert _decide(tmp_path, _PROCEED_EVIDENCE).recommended_decision is Decision.PROCEED


def test_gate12_stale_downgrades_proceed_to_inspect_first(tmp_path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")

    def _boom(root):
        raise OSError("scan failed")

    monkeypatch.setattr(igc, "python_files", _boom)  # graph build fails -> STALE
    assert _decide(tmp_path, _PROCEED_EVIDENCE).recommended_decision is Decision.INSPECT_FIRST


def test_gate12_stale_does_not_mask_stronger_gate(tmp_path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")

    def _boom(root):
        raise OSError("scan failed")

    monkeypatch.setattr(igc, "python_files", _boom)  # STALE, but a stronger gate must still win
    decision = _decide(tmp_path, _HIGH_RISK_EVIDENCE).recommended_decision
    # gate 12 only downgrades a would-be PROCEED; a higher-priority gate (ask_human/reject) is NOT
    # masked into inspect_first.
    assert decision is not Decision.INSPECT_FIRST
    assert decision in {Decision.ASK_HUMAN, Decision.REJECT}
