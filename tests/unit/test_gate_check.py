"""`pebra gate-check` — universal must-consult gate DECISION (Phase 2; no host hooks yet).

Read-only: computes {allow|deny|ask} for a proposed edit from (a) host event -> target paths,
(b) CodeGraph impact pre-filter, (c) store freshness (was this target assessed at this HEAD?).
Invariants under test: gates ONLY graph-impactful targets; fail-OPEN when graph/git/store are
unavailable; must-consult (allow once an assessment exists, regardless of its decision); and it MUST
NOT mutate the repo (never creates .pebra). Enforcement wiring is a later slice.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from pebra.adapters import candidate_binding
from pebra.adapters import gate_check_adapter as gca
from pebra.cli import gate_check as gc_cmd
from pebra.cli.main import build_parser
from pebra.core.constants import Decision
from pebra.core.gate_contract import GATE_SCHEMA_VERSION, GatePermission, GateTier


def _abs(root: Path, rel: str) -> str:
    return os.path.abspath(os.path.join(str(root), rel))


# ---- target extraction (host-specific event shapes) ---------------------------------------

def test_extract_claude_edit(tmp_path):
    fp = _abs(tmp_path, "src/a.py")
    ev = {"tool_name": "Edit", "tool_input": {"file_path": fp}, "cwd": str(tmp_path)}
    assert gca.extract_target_paths(ev) == [fp]


def test_extract_claude_multiedit_uses_top_level_file_path(tmp_path):
    # Claude MultiEdit edits[] holds edits for ONE file; the target is the top-level file_path.
    a = _abs(tmp_path, "src/a.py")
    ev = {"tool_name": "MultiEdit",
          "tool_input": {"file_path": "src/a.py",
                         "edits": [{"old_string": "x", "new_string": "y"}]},
          "cwd": str(tmp_path)}
    assert gca.extract_target_paths(ev) == [a]


def test_extract_claude_multiedit_legacy_per_edit_paths_are_best_effort(tmp_path):
    a = _abs(tmp_path, "src/a.py")
    ev = {"tool_name": "MultiEdit",
          "tool_input": {"edits": [None, "bad", {"file_path": a}]}, "cwd": str(tmp_path)}
    assert gca.extract_target_paths(ev) == [a]


def test_extract_codex_apply_patch_parses_command(tmp_path):
    patch = ("*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n"
             "*** Add File: src/b.py\n+z\n*** End Patch\n")
    ev = {"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": str(tmp_path)}
    assert gca.extract_target_paths(ev) == [_abs(tmp_path, "src/a.py"), _abs(tmp_path, "src/b.py")]


def test_extract_codex_apply_patch_parses_git_unified_diff(tmp_path):
    patch = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    ev = {"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": str(tmp_path)}
    assert gca.extract_target_paths(ev) == [_abs(tmp_path, "src/a.py")]


def test_extract_codex_apply_patch_ignores_non_string_command(tmp_path):
    ev = {"tool_name": "apply_patch", "tool_input": {"command": 123}, "cwd": str(tmp_path)}
    assert gca.extract_target_paths(ev) == []


def test_decide_denies_unparseable_apply_patch_instead_of_failing_open(tmp_path):
    patch = """diff --git a/safe.py b/safe.py
--- a/safe.py
+++ b/safe.py
@@ -1 +1 @@
-safe
+changed
--- a/.pebra/state.py
+++ b/.pebra/state.py
@@ -1 +1 @@
-state
+tampered
"""
    decision = gca.decide(
        {"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": str(tmp_path)}
    )
    assert decision.permission == "deny"
    assert decision.tier == "candidate_unverifiable"


def test_extract_non_edit_tool_is_empty(tmp_path):
    ev = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(tmp_path)}
    assert gca.extract_target_paths(ev) == []


def test_extract_malformed_event_is_empty():
    assert gca.extract_target_paths([]) == []
    assert gca.extract_target_paths({"tool_name": "Edit", "tool_input": "bad"}) == []
    assert gca.extract_target_paths({"tool_name": "Edit", "tool_input": {"file_path": "a.py"}, "cwd": []}) == [
        os.path.abspath("a.py")
    ]


# ---- path filter (exclude symbol IDs) + EXACT match ---------------------------------------

def test_filter_excludes_symbol_ids():
    files = ["src/a.py::Foo::bar", "src/a.py", "src/b.py::Baz"]
    assert gca._filter_path_entries(files) == ["src/a.py"]


def test_paths_match_is_exact_not_prefix(tmp_path):
    assert gca._paths_match(_abs(tmp_path, "src/a.py"), ["src/a.py"], str(tmp_path))
    assert not gca._paths_match(_abs(tmp_path, "src/a.py"), ["src/ab.py"], str(tmp_path))


def test_paths_match_canonicalizes_symlinked_repo_root(tmp_path):
    # A target seen through a symlink/junction (or Windows 8.3 short name) must still match a candidate
    # resolved against the real repo root — else a real assessment is missed and must_consult re-fires.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks not supported on this platform/privilege")
    assert gca._paths_match(str(link / "a.py"), ["a.py"], str(real))


# ---- store freshness lookup (seeded temp db) ----------------------------------------------

def _seed(
    db_path: Path,
    repo_id: str,
    head: str,
    files: list[str],
    decision: str = "inspect_first",
    candidate: dict | None = None,
    scores: object = None,
    candidate_replay: object = None,
):
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE assessments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "repo_id TEXT, decision TEXT, content_json TEXT)")
    binding = {"safe_scope": {"files": files}}
    if candidate is not None:
        binding["candidate"] = candidate
    content = {"assessed_commit": head, "model_guidance_packet": {"binding": binding}}
    if scores is not None:
        content["scores"] = scores
    if candidate_replay is not None:
        content["request"] = {"candidate_replay": candidate_replay}
    con.execute("INSERT INTO assessments (repo_id, decision, content_json) VALUES (?,?,?)",
                (repo_id, decision, json.dumps(content)))
    con.commit()
    con.close()


def test_query_assessments_empty_when_db_absent(tmp_path):
    assert gca._query_assessments(str(tmp_path / "nope.db"), "repo_x") == []


def test_fresh_match_true_when_head_and_path_match(tmp_path):
    db = tmp_path / "pebra.db"
    _seed(db, "repo_x", "HEAD1", ["src/a.py::Foo", "src/a.py"])
    rows = gca._query_assessments(str(db), "repo_x")
    assert gca._fresh_match(rows, [_abs(tmp_path, "src/a.py")], "HEAD1", str(tmp_path))


def test_fresh_match_false_when_head_differs(tmp_path):
    db = tmp_path / "pebra.db"
    _seed(db, "repo_x", "HEAD1", ["src/a.py"])
    rows = gca._query_assessments(str(db), "repo_x")
    assert not gca._fresh_match(rows, [_abs(tmp_path, "src/a.py")], "HEAD2", str(tmp_path))


def test_fresh_match_false_when_file_differs(tmp_path):
    db = tmp_path / "pebra.db"
    _seed(db, "repo_x", "HEAD1", ["src/a.py"])
    rows = gca._query_assessments(str(db), "repo_x")
    assert not gca._fresh_match(rows, [_abs(tmp_path, "src/b.py")], "HEAD1", str(tmp_path))


# ---- decide() — orchestration (impact + head monkeypatched to isolate logic) --------------

def _edit_event(root: Path, rel: str = "src/a.py"):
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": _abs(root, rel), "old_string": "old", "new_string": "new"},
        "cwd": str(root),
    }


def test_decide_allows_non_edit_tool(tmp_path):
    d = gca.decide({"tool_name": "Bash", "tool_input": {}, "cwd": str(tmp_path)})
    assert d.permission == "allow" and d.tier == "pass"


def test_decide_pass_when_not_impactful(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: False)
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "allow" and d.tier == "pass"


def test_pending_restriction_prevents_low_impact_scope_bypass(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: False)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    _seed(
        db,
        gca._repo_id(str(tmp_path)),
        "HEAD1",
        ["src/risky.py"],
        decision="revise_safer",
    )

    decision = gca.decide(_edit_event(tmp_path, "src/debug.py"))

    assert decision.permission == "deny"
    assert decision.tier == "must_consult"


def test_pending_restriction_clears_when_head_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: False)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD2")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    _seed(
        db,
        gca._repo_id(str(tmp_path)),
        "HEAD1",
        ["src/risky.py"],
        decision="revise_safer",
    )

    decision = gca.decide(_edit_event(tmp_path, "src/debug.py"))

    assert decision.permission == "allow"
    assert decision.tier == "pass"


def test_pending_restriction_is_not_hidden_by_assessment_query_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: False)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    repo_id = gca._repo_id(str(tmp_path))
    _seed(db, repo_id, "HEAD1", ["src/risky.py"], decision="revise_safer")
    con = sqlite3.connect(db)
    content = json.dumps({"assessed_commit": "HEAD1", "model_guidance_packet": {}})
    con.executemany(
        "INSERT INTO assessments (repo_id, decision, content_json) VALUES (?,?,?)",
        [(repo_id, "proceed", content) for _ in range(gca._QUERY_LIMIT + 1)],
    )
    con.commit()
    con.close()

    decision = gca.decide(_edit_event(tmp_path, "src/debug.py"))

    assert decision.permission == "deny"
    assert decision.tier == "must_consult"


def test_pre_restriction_assessment_cannot_authorize_later_low_impact_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: False)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    target = tmp_path / "src" / "debug.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    event = _edit_event(tmp_path, "src/debug.py")
    candidate = candidate_binding.binding_for_event(event, tmp_path)
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    repo_id = gca._repo_id(str(tmp_path))
    _seed(
        db,
        repo_id,
        "HEAD1",
        ["src/debug.py"],
        decision="proceed",
        candidate=candidate,
    )
    con = sqlite3.connect(db)
    content = json.dumps({
        "assessed_commit": "HEAD1",
        "model_guidance_packet": {"binding": {"safe_scope": {"files": ["src/risky.py"]}}},
    })
    con.execute(
        "INSERT INTO assessments (repo_id, decision, content_json) VALUES (?,?,?)",
        (repo_id, "revise_safer", content),
    )
    con.commit()
    con.close()

    decision = gca.decide(event)

    assert decision.permission == "deny"
    assert decision.tier == "must_consult"


def test_newest_restrictive_assessment_is_itself_matched(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: False)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    target = tmp_path / "src" / "debug.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    event = _edit_event(tmp_path, "src/debug.py")
    candidate = candidate_binding.binding_for_event(event, tmp_path)
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    repo_id = gca._repo_id(str(tmp_path))
    _seed(db, repo_id, "HEAD1", ["src/debug.py"], decision="proceed", candidate=candidate)
    con = sqlite3.connect(db)
    content = json.dumps({
        "assessed_commit": "HEAD1",
        "model_guidance_packet": {
            "binding": {"safe_scope": {"files": ["src/debug.py"]}, "candidate": candidate}
        },
    })
    con.execute(
        "INSERT INTO assessments (repo_id, decision, content_json) VALUES (?,?,?)",
        (repo_id, "ask_human", content),
    )
    con.commit()
    con.close()

    decision = gca.decide(event)

    assert decision.permission == "deny"
    assert decision.tier == "consulted_review_unavailable"


def test_decide_fail_open_when_graph_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: None)
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "allow" and d.tier == "fail_open" and d.warn


def test_decide_fail_open_when_head_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: None)
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "allow" and d.tier == "fail_open"


def test_decide_deny_when_store_absent_means_unassessed(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    d = gca.decide(_edit_event(tmp_path))  # no .pebra/pebra.db
    assert d.permission == "deny" and d.tier == "must_consult"
    assert d.risk_summary is None


def test_decide_fail_open_when_store_is_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not sqlite")
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "allow" and d.tier == "fail_open"


def test_decide_deny_must_consult_when_no_fresh_assessment(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    _seed(db, gca._repo_id(str(tmp_path)), "OTHER_HEAD", ["src/a.py"])
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "deny" and d.tier == "must_consult" and d.reason
    assert d.risk_summary is None


def test_decide_deny_for_codex_apply_patch_event(tmp_path, monkeypatch):
    # Codex's apply_patch event (tool_input.command = patch string) flows through the SAME decide path.
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    _seed(db, gca._repo_id(str(tmp_path)), "OTHER_HEAD", ["src/a.py"])
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch\n"
    ev = {"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": str(tmp_path)}
    d = gca.decide(ev)
    assert d.permission == "deny" and d.tier == "must_consult"


def _consulted(
    tmp_path, monkeypatch, decision: str, *,
    scores: object = None, candidate_replay: object = None,
):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old\n", encoding="utf-8")
    event = _edit_event(tmp_path)
    binding = candidate_binding.binding_for_event(event, tmp_path)
    _seed(
        db,
        gca._repo_id(str(tmp_path)),
        "HEAD1",
        ["src/a.py"],
        decision=decision,
        candidate=binding,
        scores=scores,
        candidate_replay=candidate_replay,
    )


def test_decide_allow_consulted_when_decision_is_proceed(tmp_path, monkeypatch):
    _consulted(
        tmp_path, monkeypatch, "proceed",
        scores={"expected_loss": 0.12, "benefit": 0.55, "rau": 0.29},
    )
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "allow" and d.tier == "consulted"
    assert d.risk_summary.as_dict() == {
        "decision": "proceed", "expected_loss": 0.12, "benefit": 0.55, "rau": 0.29,
    }
    assert d.matched_assessment_id == "asm_1"
    assert "matched_assessment_id" not in d.as_dict()
    assert d.as_dict(include_host_metadata=True)["matched_assessment_id"] == "asm_1"


def test_decide_denies_different_candidate_for_same_head_and_path(tmp_path, monkeypatch):
    _consulted(tmp_path, monkeypatch, "proceed")
    event = _edit_event(tmp_path)
    event["tool_input"]["new_string"] = "different"

    decision = gca.decide(event)

    assert decision.permission == "deny"
    assert decision.tier == "candidate_mismatch"
    assert "assess" in decision.reason.lower()
    assert decision.matched_assessment_id is None
    assert decision.risk_summary is None


def test_decide_denies_legacy_assessment_without_candidate_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    _seed(db, gca._repo_id(str(tmp_path)), "HEAD1", ["src/a.py"], decision="proceed")

    decision = gca.decide(_edit_event(tmp_path))

    assert decision.permission == "deny"
    assert decision.tier == "candidate_unbound"
    assert decision.matched_assessment_id is None
    assert decision.risk_summary is None


def test_decide_denies_unmaterializable_host_edit(tmp_path, monkeypatch):
    _consulted(tmp_path, monkeypatch, "proceed")
    event = _edit_event(tmp_path)
    event["tool_input"].pop("old_string")

    decision = gca.decide(event)

    assert decision.permission == "deny"
    assert decision.tier == "candidate_unverifiable"
    assert decision.risk_summary is None


def test_decide_denies_unencodable_content_instead_of_raising_to_hook_fail_open(
    tmp_path, monkeypatch
):
    _consulted(tmp_path, monkeypatch, "proceed")
    event = {
        "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": "src/a.py", "content": "\ud800"},
    }

    decision = gca.decide(event)

    assert decision.permission == "deny"
    assert decision.tier == "candidate_unverifiable"


def test_decide_requires_assessed_multifile_candidate_in_one_complete_event(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    for name in ("a.py", "b.py", "c.py"):
        target = tmp_path / "src" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old\n", encoding="utf-8")
    patch = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n-old\n+new-a\n"
        "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n"
        "@@ -1 +1 @@\n-old\n+new-b\n"
    )
    binding = candidate_binding.binding_for_patch(tmp_path, patch)
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    _seed(
        db, gca._repo_id(str(tmp_path)), "HEAD1", ["src/a.py", "src/b.py"],
        decision="proceed", candidate=binding,
    )

    def event(name, content):
        return {
            "tool_name": "Write", "cwd": str(tmp_path),
            "tool_input": {"file_path": f"src/{name}", "content": content},
        }

    partial_a = gca.decide(event("a.py", "new-a\n"))
    partial_b = gca.decide(event("b.py", "new-b\n"))
    assert partial_a.permission == partial_b.permission == "deny"
    assert partial_a.tier == partial_b.tier == "candidate_incomplete"
    assert partial_a.risk_summary is partial_b.risk_summary is None

    atomic = gca.decide({
        "tool_name": "apply_patch", "cwd": str(tmp_path),
        "tool_input": {"command": patch},
    })
    assert atomic.permission == "allow" and atomic.tier == "consulted"
    extra = gca.decide(event("c.py", "new-c\n"))
    assert extra.permission == "deny"
    assert extra.tier == "must_consult"


def test_decide_returns_exact_candidate_when_assessment_is_reject(tmp_path, monkeypatch):
    _consulted(
        tmp_path, monkeypatch, "reject",
        scores={"expected_loss": 0.61, "benefit": 0.34, "rau": -0.27},
    )
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "deny" and d.tier == "consulted_review" and d.reason
    assert d.risk_summary.decision is Decision.REJECT


def test_decide_ask_when_matched_assessment_is_ask_human(tmp_path, monkeypatch):
    _consulted(
        tmp_path, monkeypatch, "ask_human",
        scores={"expected_loss": 0.61, "benefit": 0.34, "rau": -0.27},
        candidate_replay={"status": "available"},
    )
    d = gca.decide(_edit_event(tmp_path))
    assert d.permission == "ask" and d.tier == "consulted_review"
    assert d.risk_summary.decision is Decision.ASK_HUMAN
    assert "pebra accept-risk --apply" in d.reason


def test_deny_reason_is_blinding_neutral():
    # The must-consult deny reason DOES reach the A/B agent; it must carry no engine/experiment vocab
    # or the blinding pre-send scan would fail-closed and abort real runs.
    reason = gca._deny_reason(["src/a.py"], "abcdef1234").lower()
    for term in ("pebra", "codegraph", "graph", "fan-in", "percentile", "experiment", "oracle",
                 "evaluation", "trial", "blinded"):
        assert term not in reason, f"leak term {term!r} in deny reason"


def test_decide_consult_only_keeps_reject_as_consulted_review(tmp_path, monkeypatch):
    _consulted(tmp_path, monkeypatch, "reject")
    d = gca.decide(_edit_event(tmp_path), consult_only=True)
    assert d.permission == "deny" and d.tier == "consulted_review"
    assert d.reason


def test_decide_revise_safer_blocks_even_in_consult_only(tmp_path, monkeypatch):
    # revise_safer is not a human approval prompt; it blocks the current write and asks the agent to
    # resubmit a narrower candidate, so consult_only must not silently allow it.
    _consulted(tmp_path, monkeypatch, "revise_safer")
    d = gca.decide(_edit_event(tmp_path), consult_only=True)
    assert d.permission == "deny" and d.tier == "consulted_revise"
    assert "revise" in d.reason.lower()


@pytest.mark.parametrize("decision", ["inspect_first", "test_first"])
def test_decide_prerequisite_decision_blocks_write(tmp_path, monkeypatch, decision):
    _consulted(tmp_path, monkeypatch, decision)

    result = gca.decide(_edit_event(tmp_path), consult_only=True)

    assert result.permission == "deny"
    assert result.tier == "consulted_prerequisite"
    assert "reassess" in result.reason.lower()


@pytest.mark.parametrize("decision", ["inspect_first", "test_first"])
def test_low_impact_prerequisite_decision_still_blocks_write(
    tmp_path, monkeypatch, decision,
):
    _consulted(tmp_path, monkeypatch, decision)
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: False)

    result = gca.decide(_edit_event(tmp_path), consult_only=True)

    assert result.permission == "deny"
    assert result.tier == "consulted_prerequisite"


def test_decide_consult_only_blocks_ask_human(tmp_path, monkeypatch):
    _consulted(tmp_path, monkeypatch, "ask_human")
    d = gca.decide(_edit_event(tmp_path), consult_only=True)
    assert d.permission == "deny" and d.tier == "consulted_review_unavailable"


@pytest.mark.parametrize(
    "decision,tier",
    [
        ("proceed", GateTier.CONSULTED),
        ("revise_safer", GateTier.CONSULTED_REVISE),
        ("inspect_first", GateTier.CONSULTED_PREREQUISITE),
        ("test_first", GateTier.CONSULTED_PREREQUISITE),
        ("reject", GateTier.CONSULTED_REVIEW),
    ],
)
def test_exact_candidate_exposes_live_finite_risk_summary(
    tmp_path, monkeypatch, decision, tier,
):
    _consulted(
        tmp_path, monkeypatch, decision,
        scores={"expected_loss": 0.6100004, "benefit": 0.34, "rau": -0.00000027},
    )

    result = gca.decide(_edit_event(tmp_path))

    assert result.tier is tier
    assert result.risk_summary.as_dict() == {
        "decision": decision,
        "expected_loss": 0.6100004,
        "benefit": 0.34,
        "rau": -0.00000027,
    }


@pytest.mark.parametrize(
    "decision,action",
    [
        ("revise_safer", "revise"),
        ("inspect_first", "inspect"),
        ("test_first", "test"),
        ("reject", "different candidate or route"),
    ],
)
def test_exact_restrictive_reason_is_neutral_numeric_and_actionable(
    tmp_path, monkeypatch, decision, action,
):
    _consulted(
        tmp_path, monkeypatch, decision,
        scores={"expected_loss": 0.6100004, "benefit": 0.34, "rau": -0.00000027},
    )

    result = gca.decide(_edit_event(tmp_path))
    reason = result.reason

    assert reason.startswith("This exact candidate is held—not your requested goal.")
    assert f"Assessment decision: {decision}." in reason
    assert "Expected loss: 0.61; benefit: 0.34; RAU: -2.7e-07." in reason
    assert action in reason.lower()
    for forbidden in ("permission denied", "goal rejected", "disobey"):
        assert forbidden not in reason.lower()


@pytest.mark.parametrize(
    "scores",
    [
        None,
        {},
        {"expected_loss": 0.61},
        {"expected_loss": 0.61, "benefit": 0.34},
        {"expected_loss": "0.61", "benefit": 0.34, "rau": -0.27},
        {"expected_loss": 0.61, "benefit": float("nan"), "rau": -0.27},
        {"expected_loss": 0.61, "benefit": 0.34, "rau": float("inf")},
    ],
)
def test_malformed_exact_scores_keep_restriction_without_partial_fragments(
    tmp_path, monkeypatch, scores,
):
    _consulted(tmp_path, monkeypatch, "revise_safer", scores=scores)

    result = gca.decide(_edit_event(tmp_path))

    assert result.permission is GatePermission.RETURN_CANDIDATE
    assert result.tier is GateTier.CONSULTED_REVISE
    assert result.risk_summary is None
    assert "risk summary unavailable" in result.reason.lower()
    for fragment in ("0.61", "0.34", "-0.27", "nan", "inf"):
        assert fragment not in result.reason.lower()


@pytest.mark.parametrize(
    "replay",
    [None, {}, "bad", {"status": "not_applicable"}, {"status": "consumed"}, {"status": 1}],
)
def test_ask_human_without_available_replay_returns_candidate(
    tmp_path, monkeypatch, replay,
):
    _consulted(
        tmp_path, monkeypatch, "ask_human",
        scores={"expected_loss": 0.61, "benefit": 0.34, "rau": -0.27},
        candidate_replay=replay,
    )

    result = gca.decide(_edit_event(tmp_path))

    assert result.permission is GatePermission.RETURN_CANDIDATE
    assert result.tier is GateTier.CONSULTED_REVIEW_UNAVAILABLE
    assert result.risk_summary.decision is Decision.ASK_HUMAN
    assert "pebra accept-risk" not in result.reason
    assert "reassess" in result.reason.lower() or "another route" in result.reason.lower()


def test_consult_only_ask_human_never_exposes_product_or_approval_command(tmp_path, monkeypatch):
    _consulted(
        tmp_path, monkeypatch, "ask_human",
        scores={"expected_loss": 0.61, "benefit": 0.34, "rau": -0.27},
        candidate_replay={"status": "available"},
    )

    result = gca.decide(_edit_event(tmp_path), consult_only=True)

    assert result.permission is GatePermission.RETURN_CANDIDATE
    assert result.tier is GateTier.CONSULTED_REVIEW_UNAVAILABLE
    assert "no trusted human approver is available" in result.reason.lower()
    assert "pebra" not in result.reason.lower()


def test_non_exact_paths_never_expose_a_risk_summary(tmp_path, monkeypatch):
    _consulted(
        tmp_path, monkeypatch, "proceed",
        scores={"expected_loss": 0.12, "benefit": 0.55, "rau": 0.29},
    )
    event = _edit_event(tmp_path)
    event["tool_input"]["new_string"] = "different"
    assert gca.decide(event).risk_summary is None


@pytest.mark.parametrize(
    "decision,permission,tier,replay",
    [
        ("proceed", GatePermission.CONTINUE, GateTier.CONSULTED, None),
        ("revise_safer", GatePermission.RETURN_CANDIDATE, GateTier.CONSULTED_REVISE, None),
        (
            "inspect_first",
            GatePermission.RETURN_CANDIDATE,
            GateTier.CONSULTED_PREREQUISITE,
            None,
        ),
        (
            "test_first",
            GatePermission.RETURN_CANDIDATE,
            GateTier.CONSULTED_PREREQUISITE,
            None,
        ),
        ("reject", GatePermission.RETURN_CANDIDATE, GateTier.CONSULTED_REVIEW, None),
        (
            "ask_human",
            GatePermission.REQUEST_HUMAN,
            GateTier.CONSULTED_REVIEW,
            {"status": "available"},
        ),
    ],
)
def test_persisted_decision_mapping_survives_unavailable_risk_summary(
    tmp_path, monkeypatch, decision, permission, tier, replay,
):
    _consulted(tmp_path, monkeypatch, decision, candidate_replay=replay)

    result = gca.decide(_edit_event(tmp_path))

    assert result.permission is permission
    assert result.tier is tier
    assert result.risk_summary is None


def test_gate_check_envelope_is_versioned_and_nullable(monkeypatch, capsys):
    monkeypatch.setattr(gca, "decide", lambda event, **kwargs: gca.GateDecision("allow", "pass"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Bash"})))

    gc_cmd.run_gate_check(build_parser().parse_args(["gate-check"]))

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == GATE_SCHEMA_VERSION
    assert payload["risk_summary"] is None


def test_older_exact_candidate_is_not_shadowed_by_newer_different_candidate(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: True)
    monkeypatch.setattr(gca, "_head_sha", lambda root: "HEAD1")
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    event = _edit_event(tmp_path)
    exact = candidate_binding.binding_for_event(event, tmp_path)
    different_event = _edit_event(tmp_path)
    different_event["tool_input"]["new_string"] = "different"
    different = candidate_binding.binding_for_event(different_event, tmp_path)
    db = tmp_path / ".pebra" / "pebra.db"
    db.parent.mkdir(parents=True)
    repo_id = gca._repo_id(str(tmp_path))
    _seed(db, repo_id, "HEAD1", ["src/a.py"], decision="proceed", candidate=exact)
    con = sqlite3.connect(db)
    content = {
        "assessed_commit": "HEAD1",
        "model_guidance_packet": {
            "binding": {
                "safe_scope": {"files": ["src/a.py"]},
                "candidate": different,
            }
        },
    }
    con.execute(
        "INSERT INTO assessments (repo_id, decision, content_json) VALUES (?,?,?)",
        (repo_id, "ask_human", json.dumps(content)),
    )
    con.commit()
    con.close()

    result = gca.decide(event)

    assert result.permission == "allow"
    assert result.matched_assessment_id == "asm_1"


def test_reject_reason_routes_to_different_candidate_not_risk_acceptance(tmp_path, monkeypatch):
    _consulted(tmp_path, monkeypatch, "reject")
    reason = gca.decide(_edit_event(tmp_path)).reason.lower()
    assert "different candidate or route" in reason
    assert "approve" not in reason
    assert "accept-risk" not in reason


def test_gate_check_cli_passes_consult_only(monkeypatch, capsys):
    captured = {}

    def fake_decide(event, *, db_path=None, consult_only=False):
        captured["consult_only"] = consult_only
        return gca.GateDecision("allow", "pass")

    monkeypatch.setattr(gca, "decide", fake_decide)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Edit", "tool_input": {"file_path": "a"}, "cwd": "."})))
    gc_cmd.run_gate_check(build_parser().parse_args(["gate-check", "--consult-only"]))
    assert captured["consult_only"] is True


def test_decide_never_mutates_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: None)
    gca.decide(_edit_event(tmp_path))
    assert not (tmp_path / ".pebra").exists()  # read-only gate; must NOT create .pebra


# ---- CLI surface --------------------------------------------------------------------------

def test_gate_check_is_registered():
    args = build_parser().parse_args(["gate-check"])
    assert args.func is gc_cmd.run_gate_check


def test_gate_check_cli_reads_stdin_emits_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gca, "_any_impactful", lambda targets, root: None)  # fail-open
    ev = {"tool_name": "Edit", "tool_input": {"file_path": _abs(tmp_path, "a.py")}, "cwd": str(tmp_path)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ev)))
    rc = gc_cmd.run_gate_check(build_parser().parse_args(["gate-check"]))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["permission"] == "allow" and out["tier"] == "fail_open"


def test_gate_check_cli_fail_open_on_bad_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    rc = gc_cmd.run_gate_check(build_parser().parse_args(["gate-check"]))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["permission"] == "allow"


def test_gate_check_cli_fail_open_on_non_object_json(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    rc = gc_cmd.run_gate_check(build_parser().parse_args(["gate-check"]))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["permission"] == "allow" and out["tier"] == "fail_open"


# ---- god_node anchor leg (import_graph.json) + fail-open on malformed ----------------------

def _write_import_graph(root: Path, scores: dict) -> None:
    p = root / ".pebra" / "import_graph.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"god_node_scores": scores}), encoding="utf-8")


def test_god_node_score_reads_anchor(tmp_path):
    _write_import_graph(tmp_path, {"src/a.py": 0.95})
    assert gca._god_node_score(_abs(tmp_path, "src/a.py"), str(tmp_path)) == 0.95


def test_god_node_score_absent_file_is_zero(tmp_path):
    _write_import_graph(tmp_path, {"src/a.py": 0.95})
    assert gca._god_node_score(_abs(tmp_path, "src/other.py"), str(tmp_path)) == 0.0


def test_god_node_score_none_when_import_graph_absent(tmp_path):
    assert gca._god_node_score(_abs(tmp_path, "src/a.py"), str(tmp_path)) is None


def test_god_node_score_no_crash_on_non_dict_json(tmp_path):
    # valid JSON whose root is not a dict must fail-open (return None), never raise AttributeError.
    p = tmp_path / ".pebra" / "import_graph.json"
    p.parent.mkdir(parents=True)
    p.write_text("null", encoding="utf-8")
    assert gca._god_node_score(_abs(tmp_path, "src/a.py"), str(tmp_path)) is None


def test_any_impactful_true_via_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_fanin_percentile", lambda t, r: None)  # no codegraph evidence
    _write_import_graph(tmp_path, {"src/a.py": 0.95})
    result = gca._any_impactful([_abs(tmp_path, "src/a.py")], str(tmp_path))
    assert result.impactful is True


def test_any_impactful_none_when_no_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_fanin_percentile", lambda t, r: None)  # no codegraph, no import graph
    result = gca._any_impactful([_abs(tmp_path, "src/a.py")], str(tmp_path))
    assert result.impactful is None
    assert result.fallback_reason


def test_decide_graph_fail_open_warning_preserves_adapter_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gca,
        "_any_impactful",
        lambda targets, root: gca.ImpactEvidence(
            None, "codegraph index stale; run pebra setup-graph --fix"
        ),
    )

    decision = gca.decide(_edit_event(tmp_path))

    assert decision.permission == "allow" and decision.tier == "fail_open"
    assert "index stale" in decision.warn


def test_graph_warning_never_echoes_raw_adapter_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(gca, "_fanin_percentile", lambda target, root: None)

    class _Adapter:
        def file_fanin_rollup(self, target, root):
            return SimpleNamespace(
                fallback_reason=r"codegraph DB could not be opened: C:\\secret\\pebra\\graph.db"
            )

    monkeypatch.setattr(gca, "CodeGraphAdapter", _Adapter)

    result = gca._any_impactful([_abs(tmp_path, "src/a.py")], str(tmp_path))

    assert result.fallback_reason == "CodeGraph database unreadable"
    assert "secret" not in result.fallback_reason


# ---- corrupt store rows must fail-open, not crash -----------------------------------------

def test_fresh_match_no_crash_on_corrupt_files_list(tmp_path):
    db = tmp_path / "pebra.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE assessments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "repo_id TEXT, decision TEXT, content_json TEXT)")
    content = {"assessed_commit": "HEAD1",
               "model_guidance_packet": {"binding": {"safe_scope": {"files": [None, "src/a.py"]}}}}
    con.execute("INSERT INTO assessments (repo_id, decision, content_json) VALUES (?,?,?)",
                ("repo_x", "proceed", json.dumps(content)))
    con.commit()
    con.close()
    rows = gca._query_assessments(str(db), "repo_x")
    # must not raise; the null entry is tolerated and src/a.py still matches.
    assert gca._fresh_match(rows, [_abs(tmp_path, "src/a.py")], "HEAD1", str(tmp_path))


# ---- multi-target freshness requires ALL targets covered by ONE row -----------------------

def test_fresh_match_requires_all_targets_covered(tmp_path):
    db = tmp_path / "pebra.db"
    _seed(db, "repo_x", "HEAD1", ["src/a.py"])  # covers only a
    rows = gca._query_assessments(str(db), "repo_x")
    a, b = _abs(tmp_path, "src/a.py"), _abs(tmp_path, "src/b.py")
    assert gca._fresh_match(rows, [a], "HEAD1", str(tmp_path))
    assert not gca._fresh_match(rows, [a, b], "HEAD1", str(tmp_path))  # b uncovered -> no match


def test_repo_id_matches_registry_formula(tmp_path):
    import hashlib
    root = str(tmp_path)
    expected = "repo_" + hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]
    assert gca._repo_id(root) == expected  # parity with RepositoryRegistry.resolve


# ---- codegraph method: honest None without a graph ----------------------------------------

def test_highest_file_fanin_percentile_none_without_graph(tmp_path):
    from pebra.adapters.codegraph_adapter import CodeGraphAdapter
    got = CodeGraphAdapter().highest_file_fanin_percentile(_abs(tmp_path, "x.py"), str(tmp_path))
    assert got is None
