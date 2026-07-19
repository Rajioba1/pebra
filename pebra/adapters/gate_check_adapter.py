"""gate_check_adapter — the universal, read-only "was this edit assessed?" gate DECISION.

This is the single shared primitive every enforcement adapter (Claude PreToolUse hook, Codex
apply_patch hook, the A/B experiment's write dispatch, a pre-commit gate) wraps, so production and the
experiment can never drift. It answers one question for a proposed edit: allow / deny / ask.

Phase 2 = MUST-CONSULT: a graph-IMPACTFUL target with no fresh assessment for the current
(repo_id, HEAD, path) is DENIED once (the agent must run ``pebra assess``, then re-issue).

Exact restrictive assessments hold only the attempted candidate, not the user's goal. ``reject`` asks
for another candidate or route. ``ask_human`` uses the bound risk-acceptance workflow only when replay
is available; consult-only and unavailable-replay paths return the candidate for reassessment.

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
from pebra.adapters.candidate_replay_cache import (
    CandidateReplayError,
    validate_candidate_replay_metadata,
)
from pebra.adapters.codegraph_adapter import CodeGraphAdapter
from pebra.adapters.patch_header_adapter import touched_files
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM
from pebra.core.constants import Decision
from pebra.core.gate_contract import (
    ALLOWED_PERMISSION_TIERS,
    ALLOWED_RISK_DECISIONS,
    GATE_SCHEMA_VERSION,
    _ASSESSMENT_ID_RE,
    GatePermission,
    GateRiskSummary,
    GateTier,
)

_IMPACT_THRESHOLD = 0.90  # matches modify_risk_model._HIGH_FANIN_THRESHOLD
_ANCHOR_THRESHOLD = 0.75  # matches destructive_op_model._GOD_NODE_THRESHOLD (import-graph god_node)
_QUERY_LIMIT = 200
_IMPORT_GRAPH_REL = Path(".pebra") / "import_graph.json"

# Persisted decision groups used to preserve assessment semantics at the gate.
_REVIEW_DECISIONS = frozenset({Decision.ASK_HUMAN, Decision.REJECT})
_REVISE_DECISIONS = frozenset({Decision.REVISE_SAFER})
_PREREQUISITE_DECISIONS = frozenset({Decision.INSPECT_FIRST, Decision.TEST_FIRST})
_EDIT_TOOLS = ("Edit", "Write")
_APPLY_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class GateDecision:
    permission: GatePermission | str
    tier: GateTier | str
    reason: str | None = None
    warn: str | None = None
    risk_summary: GateRiskSummary | None = None
    matched_assessment_id: str | None = None

    def __post_init__(self) -> None:
        permission = GatePermission(self.permission)
        tier = GateTier(self.tier)
        if tier not in ALLOWED_PERMISSION_TIERS[permission]:
            raise ValueError(f"undeclared gate permission/tier pair: {permission}/{tier}")
        if permission is not GatePermission.CONTINUE and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise ValueError("restrictive gate decisions require an actionable reason")
        if self.risk_summary is not None and self.risk_summary.decision not in (
            ALLOWED_RISK_DECISIONS.get((permission, tier), frozenset())
        ):
            raise ValueError("gate risk summary decision does not match its permission/tier")
        if self.risk_summary is not None and (
            not isinstance(self.matched_assessment_id, str)
            or _ASSESSMENT_ID_RE.fullmatch(self.matched_assessment_id) is None
        ):
            raise ValueError("gate risk summary requires an exact matched assessment id")
        object.__setattr__(self, "permission", permission)
        object.__setattr__(self, "tier", tier)

    def as_dict(self, *, include_host_metadata: bool = False) -> dict[str, Any]:
        payload = {
            "schema_version": GATE_SCHEMA_VERSION,
            "permission": self.permission.value,
            "tier": self.tier.value,
            "reason": self.reason,
            "warn": self.warn,
            "risk_summary": (
                self.risk_summary.as_dict() if self.risk_summary is not None else None
            ),
        }
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
                GatePermission.RETURN_CANDIDATE, GateTier.CANDIDATE_UNVERIFIABLE,
                reason="The attempted patch could not be parsed into a complete, safe file scope. "
                "Assess and apply a well-formed atomic patch.",
            )
        return GateDecision(GatePermission.CONTINUE, GateTier.PASS)
    try:
        repo_root = str(paths.find_repo_root(event.get("cwd") or "."))
    except Exception as exc:  # noqa: BLE001 - resolution failure must fail open, never crash a host edit
        return GateDecision(
            GatePermission.CONTINUE,
            GateTier.FAIL_OPEN,
            warn=f"gate: repo root unresolved: {exc}",
        )

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
                    GatePermission.CONTINUE, GateTier.FAIL_OPEN,
                    warn=f"gate: graph unavailable{detail}; skipping consult check",
                )
            return GateDecision(GatePermission.CONTINUE, GateTier.PASS)
        pending = _query_pending_restriction(db, _repo_id(repo_root), head)
        if pending is None:
            return GateDecision(
                GatePermission.CONTINUE,
                GateTier.FAIL_OPEN,
                warn="gate: assessment store unavailable",
            )
        if not pending:
            if impactful is None:
                detail = f": {impact_reason}" if impact_reason else ""
                return GateDecision(
                    GatePermission.CONTINUE, GateTier.FAIL_OPEN,
                    warn=f"gate: graph unavailable{detail}; skipping consult check",
                )
            return GateDecision(GatePermission.CONTINUE, GateTier.PASS)
        rows = _query_assessments(db, _repo_id(repo_root))
        if rows is None:
            return GateDecision(
                GatePermission.CONTINUE,
                GateTier.FAIL_OPEN,
                warn="gate: assessment store unavailable",
            )

    head = head or _head_sha(repo_root)
    if head is None:
        return GateDecision(
            GatePermission.CONTINUE,
            GateTier.FAIL_OPEN,
            warn="gate: git HEAD unavailable; skipping consult check",
        )

    rows = rows if rows is not None else _query_assessments(db, _repo_id(repo_root))
    if rows is None:
        return GateDecision(
            GatePermission.CONTINUE,
            GateTier.FAIL_OPEN,
            warn="gate: assessment store unavailable",
        )
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
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.MUST_CONSULT,
            reason=_deny_reason(targets, head),
        )
    matched_id = f"asm_{int(matched['id'])}"
    expected_candidate = _candidate_binding(matched)
    if expected_candidate is None:
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CANDIDATE_UNBOUND,
            reason=_candidate_reason(targets, head),
        )
    if attempted_candidate is None:
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CANDIDATE_UNVERIFIABLE,
            reason=_candidate_reason(targets, head),
        )
    attempted_files = attempted_candidate.get("files") or {}
    expected_files = expected_candidate.get("files") or {}
    if attempted_candidate.get("algorithm") != expected_candidate.get("algorithm") or not attempted_files:
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CANDIDATE_MISMATCH,
            reason=_candidate_reason(targets, head),
        )
    if set(attempted_files) != set(expected_files):
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CANDIDATE_INCOMPLETE,
            reason=_candidate_incomplete_reason(targets, head),
        )
    if any(expected_files.get(path) != digest for path, digest in attempted_files.items()):
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CANDIDATE_MISMATCH,
            reason=_candidate_reason(targets, head),
        )
    try:
        assessment_decision = Decision(matched.get("decision"))
    except (TypeError, ValueError):
        return GateDecision(
            GatePermission.CONTINUE,
            GateTier.FAIL_OPEN,
            warn="gate: persisted decision failed data-integrity validation",
        )
    risk_summary = _risk_summary(matched)
    if assessment_decision in _REVISE_DECISIONS:
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CONSULTED_REVISE,
            reason=_exact_restrictive_reason(assessment_decision.value, risk_summary),
            risk_summary=risk_summary,
            matched_assessment_id=matched_id,
        )
    if assessment_decision in _PREREQUISITE_DECISIONS:
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CONSULTED_PREREQUISITE,
            reason=_exact_restrictive_reason(assessment_decision.value, risk_summary),
            risk_summary=risk_summary,
            matched_assessment_id=matched_id,
        )
    if assessment_decision is Decision.REJECT:
        return GateDecision(
            GatePermission.RETURN_CANDIDATE,
            GateTier.CONSULTED_REVIEW,
            reason=_exact_restrictive_reason(assessment_decision.value, risk_summary),
            risk_summary=risk_summary,
            matched_assessment_id=matched_id,
        )
    if assessment_decision is Decision.ASK_HUMAN:
        replay_available = _candidate_replay_available(matched)
        if consult_only or not replay_available:
            return GateDecision(
                GatePermission.RETURN_CANDIDATE,
                GateTier.CONSULTED_REVIEW_UNAVAILABLE,
                reason=_exact_restrictive_reason(
                    assessment_decision.value,
                    risk_summary,
                    consult_only=consult_only,
                    replay_available=replay_available,
                ),
                risk_summary=risk_summary,
                matched_assessment_id=matched_id,
            )
        return GateDecision(
            GatePermission.REQUEST_HUMAN,
            GateTier.CONSULTED_REVIEW,
            reason=_exact_restrictive_reason(
                assessment_decision.value,
                risk_summary,
                replay_available=True,
            ),
            risk_summary=risk_summary,
            matched_assessment_id=matched_id,
        )
    if assessment_decision is Decision.PROCEED:
        return GateDecision(
            GatePermission.CONTINUE,
            GateTier.CONSULTED,
            risk_summary=risk_summary,
            matched_assessment_id=matched_id,
        )
    raise AssertionError(f"unhandled persisted decision: {assessment_decision}")


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


def _risk_summary(row: dict[str, Any]) -> GateRiskSummary | None:
    """Build an all-or-none summary from the already exact-matched persisted row."""
    try:
        content = json.loads(row["content_json"])
        scores = content["scores"]
        if not isinstance(scores, dict):
            return None
        return GateRiskSummary(
            decision=row["decision"],
            expected_loss=scores["expected_loss"],
            benefit=scores["benefit"],
            rau=scores["rau"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _candidate_replay_available(row: dict[str, Any]) -> bool:
    try:
        content = json.loads(row["content_json"])
        replay = content["request"]["candidate_replay"]
        validate_candidate_replay_metadata(replay)
        return True
    except (CandidateReplayError, KeyError, TypeError, ValueError):
        return False


def _exact_restrictive_reason(
    decision: str,
    summary: GateRiskSummary | None,
    *,
    consult_only: bool = False,
    replay_available: bool = False,
) -> str:
    prefix = "This exact candidate is held—not your requested goal. "
    if summary is None:
        evidence = f"Assessment decision: {decision}; risk summary unavailable. "
    else:
        evidence = (
            f"Assessment decision: {summary.decision.value}. "
            f"Expected loss: {summary.expected_loss:.6g}; benefit: {summary.benefit:.6g}; "
            f"RAU: {summary.rau:+.6g}. "
        )
    if decision == "revise_safer":
        action = "Next action: revise this candidate to be safer, then reassess it."
    elif decision == "inspect_first":
        action = "Next action: inspect the affected behavior, then reassess this candidate."
    elif decision == "test_first":
        action = "Next action: test the affected behavior, then reassess this candidate."
    elif decision == "reject":
        action = "Next action: choose a different candidate or route."
    elif consult_only:
        action = (
            "No trusted human approver is available; reassess this candidate or choose another route."
        )
    elif replay_available:
        action = "Next action: run the bound human-review workflow: pebra accept-risk --apply."
    else:
        action = (
            "Bound application is unavailable; reassess this candidate or choose another route."
        )
    return prefix + evidence + action


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
            decision.value
            for decision in _REVISE_DECISIONS | _REVIEW_DECISIONS | _PREREQUISITE_DECISIONS
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
