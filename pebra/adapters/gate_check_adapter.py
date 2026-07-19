"""gate_check_adapter — the universal, read-only "was this edit assessed?" gate DECISION.

This is the single shared primitive every enforcement adapter (Claude PreToolUse hook, Codex
apply_patch hook, the A/B experiment's write dispatch, a pre-commit gate) wraps, so production and the
experiment can never drift. It answers one question for a proposed edit: allow / deny / ask.

Phase 2 = MUST-CONSULT: a graph-IMPACTFUL target with no fresh assessment for the current
(repo_id, HEAD, path) is DENIED once (the agent must run ``pebra assess``, then re-issue).

Phase 6 = ASK-ONLY verdict tier: once a matching assessment exists, ``reject`` / ``ask_human`` become
host-overridable ASK in interactive hosts. In humanless ``consult_only`` hosts, there is no approver to
ask, so the conservative fallback is DENY. ``revise_safer`` is different: it blocks the current write
and asks the agent to resubmit a narrower candidate, so it remains active in both modes.

Hard invariants:
- **Read-only**: computes repo_id via ``paths.find_repo_root`` + sha1 directly; it must NEVER call
  ``RepositoryRegistry.resolve`` (which runs ``ensure_pebra_dir`` and would create ``.pebra/``).
  Store access is a raw read-only sqlite connection (``?mode=ro``) — importing
  ``SqliteStore`` would create the db file on connect and break fail-open.
- **Fail-open**: graph absent / git error / unreadable store / infrastructure parse error -> allow (+ a
  warning). A missing store means "not assessed" and denies an impactful edit; candidate mismatch or
  an unmaterializable host edit also denies and requires reassessment.
- **Graph-impactful targets are gated by default** (high per-symbol fan-in OR architecture anchor).
  After a restrictive assessment at the same HEAD, all edits must consult until the candidate is
  applied and committed; this prevents switching to a low-impact file to bypass a pending restriction.

Boundaries ("one rule"): ADAPTER — imports ``pebra.core``/``pebra.ports``/sibling adapters + stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pebra.adapters import candidate_binding, paths
from pebra.adapters.codegraph_adapter import CodeGraphAdapter
from pebra.adapters.patch_header_adapter import touched_files
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM

_IMPACT_THRESHOLD = 0.90  # matches modify_risk_model._HIGH_FANIN_THRESHOLD
_ANCHOR_THRESHOLD = 0.75  # matches destructive_op_model._GOD_NODE_THRESHOLD (import-graph god_node)
_QUERY_LIMIT = 200
_IMPORT_GRAPH_REL = Path(".pebra") / "import_graph.json"

# Engine verdicts that, once consulted, escalate to a host-approval ASK (Phase 6 verdict tier).
_REVIEW_DECISIONS = frozenset({"ask_human", "reject"})
_REVISE_DECISIONS = frozenset({"revise_safer"})
_PREREQUISITE_DECISIONS = frozenset({"inspect_first", "test_first"})
_EDIT_TOOLS = ("Edit", "Write")
_APPLY_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class GateDecision:
    permission: str            # "allow" | "deny" | "ask"
    tier: str                  # "pass" | "must_consult" | "consulted" | "fail_open"
    reason: str | None = None  # actionable text for a deny/ask
    warn: str | None = None    # diagnostic for a fail-open path
    matched_assessment_id: str | None = None  # host attribution; omitted from model-facing output

    def as_dict(self, *, include_host_metadata: bool = False) -> dict[str, Any]:
        payload = {"permission": self.permission, "tier": self.tier,
                   "reason": self.reason, "warn": self.warn}
        if include_host_metadata:
            payload["matched_assessment_id"] = self.matched_assessment_id
        return payload


@dataclass(frozen=True)
class ImpactEvidence:
    """Graph impact result plus the reason an unavailable graph could not answer."""

    impactful: bool | None
    fallback_reason: str | None = None


# ---- host event -> target paths -----------------------------------------------------------

def extract_target_paths(event: dict[str, Any]) -> list[str]:
    """Absolute target file paths from a PreToolUse-style event, per host tool shape."""
    if not isinstance(event, dict):
        return []
    name = event.get("tool_name", "")
    ti = event.get("tool_input") or {}
    if not isinstance(ti, dict):
        return []
    cwd = event.get("cwd") or "."
    if not isinstance(cwd, str):
        cwd = "."

    def _abs(p: str) -> str:
        return os.path.abspath(os.path.join(cwd, p))

    if name in _EDIT_TOOLS:
        fp = ti.get("file_path")
        return [_abs(fp)] if isinstance(fp, str) and fp else []
    if name == "MultiEdit":
        fp = ti.get("file_path")
        if isinstance(fp, str) and fp:
            return [_abs(fp)]
        # Legacy/best-effort fallback for synthetic hosts that may attach a file path per edit.
        out: list[str] = []
        for edit in ti.get("edits") or []:
            if not isinstance(edit, dict):
                continue
            fp = edit.get("file_path")
            if isinstance(fp, str) and fp:
                out.append(_abs(fp))
        return out
    if name == "apply_patch":  # Codex: tool_input.command is the patch string (no file_path)
        command = ti.get("command") or ""
        if not isinstance(command, str):
            return []
        patch_paths = [p for p in _APPLY_PATCH_FILE_RE.findall(command) if p]
        if not patch_paths:
            patch_paths = list(touched_files(command))
        return [_abs(p) for p in patch_paths]
    return []


# ---- decision ------------------------------------------------------------------------------

def decide(event: dict[str, Any], *, db_path: str | None = None, consult_only: bool = False) -> GateDecision:
    targets = extract_target_paths(event)
    if not targets:
        tool_input = event.get("tool_input") if isinstance(event, dict) else None
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if (
            isinstance(event, dict)
            and event.get("tool_name") == "apply_patch"
            and isinstance(command, str)
            and command.strip()
        ):
            return GateDecision(
                "deny", "candidate_unverifiable",
                reason="The attempted patch could not be parsed into a complete, safe file scope. "
                "Assess and apply a well-formed atomic patch.",
            )
        return GateDecision("allow", "pass")
    try:
        repo_root = str(paths.find_repo_root(event.get("cwd") or "."))
    except Exception as exc:  # noqa: BLE001 - resolution failure must fail open, never crash a host edit
        return GateDecision("allow", "fail_open", warn=f"gate: repo root unresolved: {exc}")

    impact_result = _any_impactful(targets, repo_root)
    # Keep compatibility with simple injected bools in host tests while the real adapter always
    # returns ImpactEvidence so an unavailable graph retains its diagnostic reason.
    if isinstance(impact_result, ImpactEvidence):
        impactful = impact_result.impactful
        impact_reason = impact_result.fallback_reason
    else:
        impactful = impact_result
        impact_reason = None
    head: str | None = None
    rows: list[dict[str, Any]] | None = None
    db = db_path or str(Path(repo_root) / ".pebra" / "pebra.db")
    if impactful is not True:
        head = _head_sha(repo_root)
        if head is None:
            if impactful is None:
                detail = f": {impact_reason}" if impact_reason else ""
                return GateDecision(
                    "allow", "fail_open",
                    warn=f"gate: graph unavailable{detail}; skipping consult check",
                )
            return GateDecision("allow", "pass")
        pending = _query_pending_restriction(db, _repo_id(repo_root), head)
        if pending is None:
            return GateDecision("allow", "fail_open", warn="gate: assessment store unavailable")
        if not pending:
            if impactful is None:
                detail = f": {impact_reason}" if impact_reason else ""
                return GateDecision(
                    "allow", "fail_open",
                    warn=f"gate: graph unavailable{detail}; skipping consult check",
                )
            return GateDecision("allow", "pass")
        rows = _query_assessments(db, _repo_id(repo_root))
        if rows is None:
            return GateDecision("allow", "fail_open", warn="gate: assessment store unavailable")

    head = head or _head_sha(repo_root)
    if head is None:
        return GateDecision("allow", "fail_open", warn="gate: git HEAD unavailable; skipping consult check")

    rows = rows if rows is not None else _query_assessments(db, _repo_id(repo_root))
    if rows is None:
        return GateDecision("allow", "fail_open", warn="gate: assessment store unavailable")
    attempted_candidate = candidate_binding.binding_for_event(event, repo_root)
    matched = _matched_row(
        rows,
        targets,
        head,
        repo_root,
        newer_than_id=int(pending or 0) if impactful is not True else 0,
        attempted_candidate=attempted_candidate,
    )
    if matched is None:
        return GateDecision("deny", "must_consult", reason=_deny_reason(targets, head))
    matched_id = f"asm_{int(matched['id'])}"
    expected_candidate = _candidate_binding(matched)
    if expected_candidate is None:
        return GateDecision(
            "deny", "candidate_unbound", reason=_candidate_reason(targets, head),
        )
    if attempted_candidate is None:
        return GateDecision(
            "deny", "candidate_unverifiable", reason=_candidate_reason(targets, head),
        )
    attempted_files = attempted_candidate.get("files") or {}
    expected_files = expected_candidate.get("files") or {}
    if attempted_candidate.get("algorithm") != expected_candidate.get("algorithm") or not attempted_files:
        return GateDecision(
            "deny", "candidate_mismatch", reason=_candidate_reason(targets, head),
        )
    if set(attempted_files) != set(expected_files):
        return GateDecision(
            "deny", "candidate_incomplete", reason=_candidate_incomplete_reason(targets, head),
        )
    if any(expected_files.get(path) != digest for path, digest in attempted_files.items()):
        return GateDecision(
            "deny", "candidate_mismatch", reason=_candidate_reason(targets, head),
        )
    if str(matched.get("decision")) in _REVISE_DECISIONS:
        return GateDecision(
            "deny", "consulted_revise", reason=_revise_reason(targets, head),
            matched_assessment_id=matched_id,
        )
    if str(matched.get("decision")) in _PREREQUISITE_DECISIONS:
        return GateDecision(
            "deny", "consulted_prerequisite",
            reason=_prerequisite_reason(str(matched.get("decision")), targets, head),
            matched_assessment_id=matched_id,
        )
    # Phase 6 verdict tier: interactive hosts can ask for approval; consult-only hosts have no
    # approver, so they must stay conservative instead of silently allowing exhausted review verdicts.
    if str(matched.get("decision")) in _REVIEW_DECISIONS:
        if consult_only:
            return GateDecision(
                "deny", "consulted_review_unavailable", reason=_review_unavailable_reason(targets, head)
                , matched_assessment_id=matched_id
            )
        return GateDecision(
            "ask", "consulted_review", reason=_review_reason(targets, head),
            matched_assessment_id=matched_id,
        )
    return GateDecision("allow", "consulted", matched_assessment_id=matched_id)


def _deny_reason(targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    return (f"Consultation required before editing {names} (high-impact at commit {head[:8]}). "
            "Run the pre-edit assessment for the target file(s), then re-issue the edit.")


def _candidate_reason(targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    return (
        f"The attempted edit to {names} does not match the exact candidate assessed at commit "
        f"{head[:8]}. Assess this candidate, then re-issue the same edit."
    )


def _candidate_incomplete_reason(targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    return (
        f"The assessed candidate for {names} at commit {head[:8]} changes multiple files and must "
        "be applied atomically. Assess a single-file candidate or use an atomic patch event containing "
        "the complete assessed candidate."
    )


def _prerequisite_reason(decision: str, targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    prerequisite = "inspection" if decision == "inspect_first" else "targeted tests"
    return (
        f"Complete the required {prerequisite} for {names} at commit {head[:8]}, then reassess "
        "the exact candidate before editing."
    )


def _review_reason(targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    return (f"A pre-edit check assessed editing {names} as high-risk (commit {head[:8]}). Approve in the host "
            "prompt to proceed, or reconsider a narrower or safer change.")


def _review_unavailable_reason(targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    return (f"A pre-edit check assessed editing {names} as high-risk (commit {head[:8]}). "
            "No approval prompt is available in this host; reconsider a narrower or safer change.")


def _revise_reason(targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    return (f"Do not apply this patch to {names} at commit {head[:8]}. Submit a narrower or safer "
            "candidate that preserves the existing public surface, then assess again.")


# ---- impact pre-filter ---------------------------------------------------------------------

def _any_impactful(targets: list[str], repo_root: str) -> ImpactEvidence:
    """Return impact plus degradation reason; unavailable evidence tells the caller to fail open."""
    evidence_seen = False
    fallback_reasons: list[str] = []
    for target in targets:
        pctl, fallback_reason = _fanin_probe(target, repo_root)
        if pctl is not None:
            evidence_seen = True
            if pctl >= _IMPACT_THRESHOLD:
                return ImpactEvidence(True)
        elif fallback_reason:
            fallback_reasons.append(fallback_reason)
        anchor = _god_node_score(target, repo_root)
        if anchor is not None:
            evidence_seen = True
            if anchor >= _ANCHOR_THRESHOLD:
                return ImpactEvidence(True)
    if evidence_seen:
        return ImpactEvidence(False)
    reason = "; ".join(dict.fromkeys(fallback_reasons)) or "no graph evidence available"
    return ImpactEvidence(None, reason)


def _fanin_percentile(target: str, repo_root: str) -> float | None:
    try:
        return CodeGraphAdapter().highest_file_fanin_percentile(target, repo_root)
    except Exception:  # noqa: BLE001 - any adapter failure is "no evidence", never a crash
        return None


def _god_node_score(target: str, repo_root: str) -> float | None:
    """Read the persisted import-graph's per-file god_node_scores (raw json — never import the
    architecture adapter, which can trigger a full AST rebuild on the hot path). None if absent."""
    path = Path(repo_root) / _IMPORT_GRAPH_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):  # valid JSON but not an object -> fail-open, never crash
        return None
    scores = data.get("god_node_scores")
    if not isinstance(scores, dict):
        return None
    try:
        rel = Path(target).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return 0.0
    val = scores.get(rel, 0.0)
    return float(val) if isinstance(val, (int, float)) else 0.0


# ---- store freshness -----------------------------------------------------------------------

def _repo_id(repo_root: str) -> str:
    return "repo_" + hashlib.sha1(str(Path(repo_root).resolve()).encode("utf-8")).hexdigest()[:12]


def _head_sha(repo_root: str) -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    return out or None


def _query_assessments(db_path: str, repo_id: str) -> list[dict[str, Any]] | None:
    """Raw read-only rows for a repo (newest first). None => unreadable/corrupt (fail-open);
    [] => absent or present-but-empty (must-consult can still deny)."""
    if not Path(db_path).is_file():
        return []
    try:
        con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return None
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT id, decision, content_json FROM assessments WHERE repo_id = ? ORDER BY id DESC LIMIT ?",
            (repo_id, _QUERY_LIMIT),
        )
        return [
            {"id": r["id"], "decision": r["decision"], "content_json": r["content_json"]}
            for r in cur.fetchall()
        ]
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _query_pending_restriction(db_path: str, repo_id: str, head_sha: str) -> int | None:
    """Return newest restrictive assessment id, 0 when absent, or None when unreadable."""
    if not Path(db_path).is_file():
        return 0
    try:
        con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return None
    try:
        decisions = tuple(sorted(
            _REVISE_DECISIONS | _REVIEW_DECISIONS | _PREREQUISITE_DECISIONS
        ))
        placeholders = ",".join("?" for _ in decisions)
        cur = con.execute(
            f"SELECT id, content_json FROM assessments WHERE repo_id = ? "
            f"AND decision IN ({placeholders}) ORDER BY id DESC",
            (repo_id, *decisions),
        )
        for row in cur.fetchall():
            try:
                content = json.loads(row[1] or "{}")
            except (TypeError, ValueError):
                continue
            if content.get("assessed_commit") == head_sha:
                return int(row[0])
        return 0
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _matched_row(
    rows: list[dict[str, Any]],
    targets: list[str],
    head_sha: str,
    repo_root: str,
    *,
    newer_than_id: int = 0,
    attempted_candidate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Best assessment covering all targets, preferring an exact candidate binding."""
    path_matches: list[dict[str, Any]] = []
    for row in rows:
        if int(row.get("id") or 0) < newer_than_id:
            continue
        # A corrupt/partial row must never crash a host edit — skip it and keep the gate fail-open.
        try:
            content = json.loads(row["content_json"] or "{}")
            if content.get("assessed_commit") != head_sha:
                continue
            files = (((content.get("model_guidance_packet") or {}).get("binding") or {})
                     .get("safe_scope") or {}).get("files") or []
            candidates = _filter_path_entries(files)
            if all(_paths_match(t, candidates, repo_root) for t in targets):
                path_matches.append(row)
        except (ValueError, TypeError, AttributeError):
            continue
    if attempted_candidate is not None:
        for row in path_matches:
            if _candidate_binding(row) == attempted_candidate:
                return row
    return path_matches[0] if path_matches else None


def _candidate_binding(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        content = json.loads(row["content_json"] or "{}")
        binding = ((content.get("model_guidance_packet") or {}).get("binding") or {})
        candidate = binding.get("candidate")
        if not isinstance(candidate, dict):
            return None
        algorithm = candidate.get("algorithm")
        files = candidate.get("files")
        if algorithm != CANDIDATE_BINDING_ALGORITHM or not isinstance(files, dict) or not files:
            return None
        if not all(
            isinstance(path, str)
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)
            for path, digest in files.items()
        ):
            return None
        return {"algorithm": algorithm, "files": dict(files)}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _fanin_probe(target: str, repo_root: str) -> tuple[float | None, str | None]:
    """Return the percentile or the adapter's concrete degradation reason.

    ``highest_file_fanin_percentile`` predates provenance and returns only ``None``. On that path,
    query the same adapter's rollup to distinguish a valid zero-fan-in file from an unavailable graph.
    """
    pctl = _fanin_percentile(target, repo_root)
    if pctl is not None:
        return pctl, None
    try:
        rollup = CodeGraphAdapter().file_fanin_rollup(target, repo_root)
    except Exception:  # noqa: BLE001 - gate failure remains fail-open with a useful warning
        return None, "CodeGraph probe failed"
    if rollup.fallback_reason:
        return None, _safe_graph_fallback_reason(rollup.fallback_reason)
    return 0.0, None


def _safe_graph_fallback_reason(reason: str) -> str:
    """Map adapter diagnostics to stable, path-free model-facing messages."""
    lowered = reason.lower()
    mappings = (
        ("cli not found", "CodeGraph CLI not found"),
        ("out of range", "CodeGraph version unsupported"),
        ("not initialized", "CodeGraph index not initialized"),
        ("index stale", "CodeGraph index stale"),
        ("db not found", "CodeGraph database not found"),
        ("could not be opened", "CodeGraph database unreadable"),
        ("schema below", "CodeGraph schema unsupported"),
        ("query failed", "CodeGraph query failed"),
    )
    for marker, safe_message in mappings:
        if marker in lowered:
            return safe_message
    return "CodeGraph evidence unavailable"


def _fresh_match(rows: list[dict[str, Any]], targets: list[str], head_sha: str, repo_root: str) -> bool:
    return _matched_row(rows, targets, head_sha, repo_root) is not None


def _filter_path_entries(files: list[str]) -> list[str]:
    """safe_scope.files mixes file paths and symbol IDs (``path::Class::method``). Keep only the file
    paths — symbol IDs always contain ``::``; file paths never do. Non-string entries (corrupt rows)
    are dropped so a partially-bad ``files`` list never crashes the gate."""
    return [f for f in files if isinstance(f, str) and "::" not in f]


def _paths_match(target: str, candidates: list[str], repo_root: str) -> bool:
    t = _norm(os.path.abspath(target))
    for cand in candidates:
        cand_abs = cand if os.path.isabs(cand) else os.path.join(repo_root, cand)
        if _norm(cand_abs) == t:  # EXACT equality, never startswith (avoids foo.py::X ~ foo.py)
            return True
    return False


def _norm(path: str) -> str:
    # realpath (not just normpath) so short-name (Windows 8.3) and symlink/junction forms canonicalize
    # to the SAME string — else a legitimately-assessed target can miss its assessment and spuriously
    # re-trigger must_consult. realpath resolves the existing ancestor and keeps a non-existent leaf.
    return os.path.normcase(os.path.realpath(path))
