"""gate_check_adapter — the universal, read-only "was this edit assessed?" gate DECISION.

This is the single shared primitive every enforcement adapter (Claude PreToolUse hook, Codex
apply_patch hook, the A/B experiment's write dispatch, a pre-commit gate) wraps, so production and the
experiment can never drift. It answers one question for a proposed edit: allow / deny / ask.

Phase 2 = MUST-CONSULT only: a graph-IMPACTFUL target with no fresh assessment for the current
(repo_id, HEAD, path) is DENIED once (the agent must run ``pebra assess``, then re-issue); once an
assessment exists it is ALLOWED regardless of that assessment's decision (must-consult, NOT must-obey —
a hard verdict-block would punish a correct coordinated fix). The narrow ask/hard-deny tiers are a
later slice.

Hard invariants:
- **Read-only**: computes repo_id via ``paths.find_repo_root`` + sha1 directly; it must NEVER call
  ``RepositoryRegistry.resolve`` (which runs ``ensure_pebra_dir`` and would create ``.pebra/`` + edit
  ``.gitignore``). Store access is a raw read-only sqlite connection (``?mode=ro``) — importing
  ``SqliteStore`` would create the db file on connect and break fail-open.
- **Fail-open**: graph absent / git error / store absent / any parse error -> allow (+ a warning). The
  gate is a safety net, never a hard dependency.
- **Only graph-impactful targets are gated** (high per-symbol fan-in OR architecture anchor); trivial
  local edits pass friction-free.

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

from pebra.adapters import paths
from pebra.adapters.codegraph_adapter import CodeGraphAdapter

_IMPACT_THRESHOLD = 0.90  # matches modify_risk_model._HIGH_FANIN_THRESHOLD
_ANCHOR_THRESHOLD = 0.75  # matches destructive_op_model._GOD_NODE_THRESHOLD (import-graph god_node)
_QUERY_LIMIT = 200
_IMPORT_GRAPH_REL = Path(".pebra") / "import_graph.json"

_EDIT_TOOLS = ("Edit", "Write")
_APPLY_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class GateDecision:
    permission: str            # "allow" | "deny" | "ask"
    tier: str                  # "pass" | "must_consult" | "consulted" | "fail_open"
    reason: str | None = None  # actionable text for a deny/ask
    warn: str | None = None    # diagnostic for a fail-open path

    def as_dict(self) -> dict[str, Any]:
        return {"permission": self.permission, "tier": self.tier,
                "reason": self.reason, "warn": self.warn}


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
    if name == "MultiEdit":  # Claude: an edits[] array, not a top-level file_path
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
        return [_abs(p) for p in _APPLY_PATCH_FILE_RE.findall(command) if p]
    return []


# ---- decision ------------------------------------------------------------------------------

def decide(event: dict[str, Any], *, db_path: str | None = None) -> GateDecision:
    targets = extract_target_paths(event)
    if not targets:
        return GateDecision("allow", "pass")
    try:
        repo_root = str(paths.find_repo_root(event.get("cwd") or "."))
    except Exception as exc:  # noqa: BLE001 - resolution failure must fail open, never crash a host edit
        return GateDecision("allow", "fail_open", warn=f"gate: repo root unresolved: {exc}")

    impactful = _any_impactful(targets, repo_root)
    if impactful is None:
        return GateDecision("allow", "fail_open", warn="gate: graph unavailable; skipping consult check")
    if not impactful:
        return GateDecision("allow", "pass")

    head = _head_sha(repo_root)
    if head is None:
        return GateDecision("allow", "fail_open", warn="gate: git HEAD unavailable; skipping consult check")

    db = db_path or str(Path(repo_root) / ".pebra" / "pebra.db")
    rows = _query_assessments(db, _repo_id(repo_root))
    if rows is None:
        return GateDecision("allow", "fail_open", warn="gate: assessment store unavailable")
    if _fresh_match(rows, targets, head, repo_root):
        return GateDecision("allow", "consulted")
    return GateDecision("deny", "must_consult", reason=_deny_reason(targets, head))


def _deny_reason(targets: list[str], head: str) -> str:
    names = ", ".join(os.path.basename(t) for t in targets[:3])
    return (f"Consultation required before editing {names} (high-impact at commit {head[:8]}). "
            "Run the pre-edit assessment for the target file(s), then re-issue the edit.")


# ---- impact pre-filter ---------------------------------------------------------------------

def _any_impactful(targets: list[str], repo_root: str) -> bool | None:
    """True if any target is graph-impactful; False if all are below threshold; None if NO impact
    evidence is available at all (graph + import-graph both absent) -> caller fails open."""
    evidence_seen = False
    for target in targets:
        pctl = _fanin_percentile(target, repo_root)
        if pctl is not None:
            evidence_seen = True
            if pctl >= _IMPACT_THRESHOLD:
                return True
        anchor = _god_node_score(target, repo_root)
        if anchor is not None:
            evidence_seen = True
            if anchor >= _ANCHOR_THRESHOLD:
                return True
    return False if evidence_seen else None


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
            "SELECT decision, content_json FROM assessments WHERE repo_id = ? ORDER BY id DESC LIMIT ?",
            (repo_id, _QUERY_LIMIT),
        )
        return [{"decision": r["decision"], "content_json": r["content_json"]} for r in cur.fetchall()]
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _fresh_match(rows: list[dict[str, Any]], targets: list[str], head_sha: str, repo_root: str) -> bool:
    """True iff some assessment row covers ALL targets: same assessed_commit AND every target path is
    inside that row's (path-filtered) safe_scope.files."""
    for row in rows:
        # A corrupt/partial row must never crash a host edit — skip it and keep the gate fail-open.
        try:
            content = json.loads(row["content_json"] or "{}")
            if content.get("assessed_commit") != head_sha:
                continue
            files = (((content.get("model_guidance_packet") or {}).get("binding") or {})
                     .get("safe_scope") or {}).get("files") or []
            candidates = _filter_path_entries(files)
            if all(_paths_match(t, candidates, repo_root) for t in targets):
                return True
        except (ValueError, TypeError, AttributeError):
            continue
    return False


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
    return os.path.normcase(os.path.normpath(path))
