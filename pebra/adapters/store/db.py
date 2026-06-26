"""SqliteStore (Architecture §10, AD-24) — hash-chained, append-only store.

Adapter: owns all SQLite I/O (sqlite3 is banned in core, allowed here). Each assessment row carries a
deterministic ``content_json`` and a ``row_hash = sha256(prev_hash + content_json)`` so the chain is
tamper-evident: ``validate_chain()`` recomputes every hash and fails if any stored content was
mutated. Guidance packets are mirrored into the assessment content and checked against their side
table row. Sanction events carry their own append-only hash chain. Writes use ``BEGIN IMMEDIATE`` so
concurrent writers can't fork either chain.

Implements ``StorePort``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
from typing import Any

from pebra.core.models import AssessmentResult

GENESIS = "GENESIS"


def _row_hash(prev_hash: str, content_json: str) -> str:
    return hashlib.sha256((prev_hash + content_json).encode("utf-8")).hexdigest()


def _canonical(result: AssessmentResult, request_payload: dict[str, Any]) -> str:
    """Deterministic content for hashing (sorted keys; no wall-clock fields)."""
    content = {
        "decision": result.recommended_decision.value,
        "requires_confirmation": result.requires_confirmation,
        "risk_mode": result.risk_mode.value,
        "action_status": result.action_status.value,
        "scores": result.scores,
        "repo_id": result.repo_id,
        "repo_root": result.repo_root,
        "assessed_commit": result.assessed_commit,
        "model_guidance_packet": result.model_guidance_packet,
        "request": request_payload,
    }
    # No default= fallback: content must be natively JSON-serializable so the chain stays
    # semantically reconstructable. A non-serializable leak should raise, not be stringified.
    return json.dumps(content, sort_keys=True)


def _outcome_canonical(
    row_id: int, terminal_status: str, detail: dict[str, Any], recorded_at: str
) -> str:
    """Per-field canonical content hashed for the outcome chain (Phase 3a / AD-4). Covers the FK,
    terminal status, detail payload, and timestamp so tampering with any of them is detectable."""
    content = {
        "assessment_id": row_id,
        "terminal_status": terminal_status,
        "detail": detail,
        "recorded_at": recorded_at,
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _guardrail_canonical(row_id: int, recorded_at: str, guardrails: dict[str, Any]) -> str:
    """Per-field canonical content hashed for the guardrail chain (Architecture §10).

    Covers the identity-bearing fields — assessment_id (FK), recorded_at (timestamp), and the
    guardrails payload — so tampering with any of them is detectable.
    """
    content = {"assessment_id": row_id, "recorded_at": recorded_at, "guardrails": guardrails}
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _guidance_matches_content(content_json: str, packet_json: str | None) -> bool:
    content = json.loads(content_json)
    embedded = content.get("model_guidance_packet")
    if embedded is None:
        return packet_json is None
    if packet_json is None:
        return False
    return json.dumps(embedded, sort_keys=True) == packet_json


class SqliteStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # isolation_level=None: disable Python's implicit transaction management so our explicit
        # BEGIN IMMEDIATE / COMMIT fully owns the write transaction (no "transaction within a
        # transaction" race; concurrent writers serialize on the IMMEDIATE lock — §10).
        self._con = sqlite3.connect(db_path, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA busy_timeout=5000")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        # executescript() issues its own COMMIT before and after the script, so with
        # isolation_level=None there is no separate transaction to manage here (and no manual
        # commit is needed). All tables use CREATE TABLE IF NOT EXISTS, which is idempotent.
        self._con.executescript(
            """
            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                content_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_guidance_packets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id INTEGER NOT NULL,
                packet_json TEXT NOT NULL,
                FOREIGN KEY (assessment_id) REFERENCES assessments(id)
            );
            CREATE TABLE IF NOT EXISTS sanction_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                assessment_id TEXT,
                sanction_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                invalidated_reason TEXT,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS post_assessment_guardrails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id INTEGER NOT NULL,
                recorded_at TEXT NOT NULL,
                guardrails_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                FOREIGN KEY (assessment_id) REFERENCES assessments(id)
            );
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id INTEGER NOT NULL,
                terminal_status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                FOREIGN KEY (assessment_id) REFERENCES assessments(id)
            );
            -- one terminal outcome per assessment (AD-4): the lifecycle closes exactly once
            CREATE UNIQUE INDEX IF NOT EXISTS ux_outcomes_assessment ON outcomes(assessment_id);
            """
        )

    def _last_assessment_hash(self) -> str:
        row = self._con.execute(
            "SELECT row_hash FROM assessments ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def _last_sanction_hash(self) -> str:
        row = self._con.execute(
            "SELECT row_hash FROM sanction_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def _last_guardrail_hash(self) -> str:
        row = self._con.execute(
            "SELECT row_hash FROM post_assessment_guardrails ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def _last_outcome_hash(self) -> str:
        row = self._con.execute(
            "SELECT row_hash FROM outcomes ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def persist_assessment(
        self, result: AssessmentResult, request_payload: dict[str, Any]
    ) -> str:
        content_json = _canonical(result, request_payload)
        try:
            self._con.execute("BEGIN IMMEDIATE")
            prev_hash = self._last_assessment_hash()
            row_hash = _row_hash(prev_hash, content_json)
            cur = self._con.execute(
                "INSERT INTO assessments (repo_id, decision, content_json, prev_hash, row_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (result.repo_id, result.recommended_decision.value, content_json, prev_hash, row_hash),
            )
            assessment_id = cur.lastrowid
            if result.model_guidance_packet is not None:
                self._con.execute(
                    "INSERT INTO model_guidance_packets (assessment_id, packet_json) VALUES (?, ?)",
                    (assessment_id, json.dumps(result.model_guidance_packet, sort_keys=True)),
                )
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return f"asm_{assessment_id}"

    def record_outcome(self, assessment_id: str, status: str, detail: dict | None = None) -> None:
        """Append the terminal outcome of an assessed action (AD-4). OutcomePort impl: the assessment
        row stays immutable; this is the only terminal-status write. Its own append-only hash chain
        mirrors the guardrail chain. Raises KeyError if the assessment doesn't exist."""
        row_id = self._row_id(assessment_id)
        payload = detail or {}
        detail_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        content_json = _outcome_canonical(row_id, status, payload, recorded_at)
        try:
            self._con.execute("BEGIN IMMEDIATE")
            # existence check inside the lock so the KeyError guarantee holds under concurrent writes
            # (and we never surface a raw FK IntegrityError). The outer handler does the single rollback.
            if self._con.execute("SELECT 1 FROM assessments WHERE id = ?", (row_id,)).fetchone() is None:
                raise KeyError(f"no assessment {assessment_id!r}")
            # AD-4: the lifecycle closes exactly once — reject a second (possibly contradictory) outcome.
            if self._con.execute(
                "SELECT 1 FROM outcomes WHERE assessment_id = ?", (row_id,)
            ).fetchone() is not None:
                raise ValueError(f"assessment {assessment_id!r} already has a terminal outcome")
            prev_hash = self._last_outcome_hash()
            row_hash = _row_hash(prev_hash, content_json)
            self._con.execute(
                "INSERT INTO outcomes "
                "(assessment_id, terminal_status, detail_json, recorded_at, prev_hash, row_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row_id, status, detail_json, recorded_at, prev_hash, row_hash),
            )
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise

    def load_outcomes(self, assessment_id: str) -> list[dict[str, Any]]:
        """Terminal outcomes recorded for an assessment, oldest first (read path for verify/dashboard)."""
        row_id = self._row_id(assessment_id)
        return [
            {"terminal_status": status, "detail": json.loads(detail_json), "recorded_at": recorded_at}
            for status, detail_json, recorded_at in self._con.execute(
                "SELECT terminal_status, detail_json, recorded_at FROM outcomes "
                "WHERE assessment_id = ? ORDER BY id ASC",
                (row_id,),
            )
        ]

    # --- read-only API for the Risk Observatory dashboard (Phase 3b/5c-A). Pure SELECTs; the
    # dashboard surface calls these directly (it may import adapters, never app/core). ---

    def list_assessments(
        self, repo_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Newest-first assessment summaries for a repo (overview + history panels), each with its
        current terminal status (None = pending) so the panels don't need a detail call per row."""
        limit = max(0, min(limit, 500))  # a negative LIMIT is unbounded in SQLite — clamp it
        offset = max(0, offset)
        rows = self._con.execute(
            "SELECT a.id, a.decision, a.content_json, o.terminal_status, o.recorded_at "
            "FROM assessments a LEFT JOIN outcomes o ON o.assessment_id = a.id "
            "WHERE a.repo_id = ? ORDER BY a.id DESC LIMIT ? OFFSET ?",
            (repo_id, limit, offset),
        ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row_id, decision, content_json, terminal_status, recorded_at in rows:
            content = json.loads(content_json)
            summaries.append(
                {
                    "assessment_id": f"asm_{row_id}",
                    "decision": decision,
                    "risk_mode": content.get("risk_mode"),
                    "scores": content.get("scores", {}),
                    "assessed_commit": content.get("assessed_commit"),
                    "terminal_status": terminal_status,  # None until an outcome is recorded
                    "outcome_recorded_at": recorded_at,
                }
            )
        return summaries

    def assessment_detail(self, assessment_id: str) -> dict[str, Any]:
        """Full detail for one assessment: content + guidance packet + guardrails + outcomes."""
        row_id = self._row_id(assessment_id)
        row = self._con.execute(
            "SELECT content_json FROM assessments WHERE id = ?", (row_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no assessment {assessment_id!r}")
        content = json.loads(row[0])
        packet = self._con.execute(
            "SELECT packet_json FROM model_guidance_packets WHERE assessment_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (row_id,),
        ).fetchone()
        guardrails = [
            json.loads(g)
            for (g,) in self._con.execute(
                "SELECT guardrails_json FROM post_assessment_guardrails "
                "WHERE assessment_id = ? ORDER BY id ASC",
                (row_id,),
            )
        ]
        return {
            "assessment_id": assessment_id,
            "content": content,
            "model_guidance_packet": (
                json.loads(packet[0]) if packet else content.get("model_guidance_packet")
            ),
            "guardrails": guardrails,
            "outcomes": self.load_outcomes(assessment_id),
        }

    def chain_status(self) -> dict[str, Any]:
        """Audit-chain panel: integrity verdict + per-table row counts."""
        counts = {
            table: self._con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("assessments", "sanction_events", "post_assessment_guardrails", "outcomes")
        }
        return {"valid": self.validate_chain(), "counts": counts}

    def validate_chain(self) -> bool:
        return (
            self._validate_assessment_chain()
            and self._validate_sanction_chain()
            and self._validate_guardrail_chain()
            and self._validate_outcome_chain()
        )

    def _validate_outcome_chain(self) -> bool:
        prev_hash = GENESIS
        for assessment_id, terminal_status, detail_json, recorded_at, stored_prev, stored_hash in (
            self._con.execute(
                "SELECT assessment_id, terminal_status, detail_json, recorded_at, prev_hash, row_hash "
                "FROM outcomes ORDER BY id ASC"
            )
        ):
            if stored_prev != prev_hash:
                return False
            content_json = _outcome_canonical(
                assessment_id, terminal_status, json.loads(detail_json), recorded_at
            )
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    # Architecture §10: guardrail rows use the per-field integrity formula — the hash covers
    # assessment_id, recorded_at, and the guardrails payload (via _guardrail_canonical), so tampering
    # with any of those columns is detectable. (Assessment/sanction/guidance chains still hash their
    # own json blob; reconciling them to per-field is pending.)
    def _validate_guardrail_chain(self) -> bool:
        prev_hash = GENESIS
        for assessment_id, recorded_at, guardrails_json, stored_prev, stored_hash in self._con.execute(
            "SELECT assessment_id, recorded_at, guardrails_json, prev_hash, row_hash "
            "FROM post_assessment_guardrails ORDER BY id ASC"
        ):
            if stored_prev != prev_hash:
                return False
            content_json = _guardrail_canonical(assessment_id, recorded_at, json.loads(guardrails_json))
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def _validate_assessment_chain(self) -> bool:
        prev_hash = GENESIS
        for content_json, stored_prev, stored_hash, packet_json in self._con.execute(
            """
            SELECT assessments.content_json, assessments.prev_hash, assessments.row_hash,
                   model_guidance_packets.packet_json
            FROM assessments
            LEFT JOIN model_guidance_packets
              ON model_guidance_packets.assessment_id = assessments.id
            ORDER BY assessments.id ASC
            """
        ):
            if stored_prev != prev_hash:
                return False
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            if not _guidance_matches_content(content_json, packet_json):
                return False
            prev_hash = stored_hash
        return True

    def _validate_sanction_chain(self) -> bool:
        prev_hash = GENESIS
        for sanction_json, stored_prev, stored_hash in self._con.execute(
            "SELECT sanction_json, prev_hash, row_hash FROM sanction_events ORDER BY id ASC"
        ):
            if stored_prev != prev_hash:
                return False
            if _row_hash(prev_hash, sanction_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def create_sanction(self, repo_id: str, sanction: dict[str, Any]) -> str:
        # The integrity hash covers only the immutable sanction content; `status`/`invalidated_reason`
        # are mutable lifecycle columns (AD-26) and are intentionally NOT hashed, so invalidation
        # never breaks the chain.
        sanction_json = json.dumps(sanction, sort_keys=True)
        assessment_id = sanction.get("assessment_id")
        try:
            self._con.execute("BEGIN IMMEDIATE")
            prev_hash = self._last_sanction_hash()
            row_hash = _row_hash(prev_hash, sanction_json)
            cur = self._con.execute(
                "INSERT INTO sanction_events "
                "(repo_id, assessment_id, sanction_json, status, invalidated_reason, "
                " prev_hash, row_hash) VALUES (?, ?, ?, 'active', NULL, ?, ?)",
                (repo_id, assessment_id, sanction_json, prev_hash, row_hash),
            )
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return f"sx_{cur.lastrowid}"

    def active_sanction_for_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT sanction_json FROM sanction_events "
            "WHERE assessment_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
            (assessment_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def invalidate_sanctions_for_assessment(self, assessment_id: str, reason: str) -> list[str]:
        """Invalidate every active sanction bound to an assessment (drift). Returns their ids."""
        try:
            self._con.execute("BEGIN IMMEDIATE")
            ids = [
                r[0]
                for r in self._con.execute(
                    "SELECT id FROM sanction_events WHERE assessment_id = ? AND status = 'active'",
                    (assessment_id,),
                )
            ]
            self._con.execute(
                "UPDATE sanction_events SET status = 'invalidated', invalidated_reason = ? "
                "WHERE assessment_id = ? AND status = 'active'",
                (reason, assessment_id),
            )
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return [f"sx_{i}" for i in ids]

    @staticmethod
    def _row_id(assessment_id: str) -> int:
        parts = assessment_id.split("_", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            raise KeyError(f"invalid assessment id format: {assessment_id!r}")
        return int(parts[1])

    def load_assessment(self, assessment_id: str) -> dict[str, Any]:
        row = self._con.execute(
            "SELECT content_json FROM assessments WHERE id = ?", (self._row_id(assessment_id),)
        ).fetchone()
        if row is None:
            raise KeyError(f"no assessment {assessment_id!r}")
        return json.loads(row[0])

    def persist_guardrails(self, assessment_id: str, guardrails: dict[str, Any]) -> str:
        # Serialize like the chained writes (BEGIN IMMEDIATE) so a guardrails INSERT can't interleave
        # with an in-flight assessment write and so the FK to assessments is satisfied atomically.
        row_id = self._row_id(assessment_id)
        # compact separators match _guardrail_canonical so the stored blob is byte-reconstructable
        # against the hash basis by an external auditor (§10 reconstructability).
        guardrails_json = json.dumps(guardrails, sort_keys=True, separators=(",", ":"))
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        content_json = _guardrail_canonical(row_id, recorded_at, guardrails)
        try:
            self._con.execute("BEGIN IMMEDIATE")
            prev_hash = self._last_guardrail_hash()
            row_hash = _row_hash(prev_hash, content_json)
            cur = self._con.execute(
                "INSERT INTO post_assessment_guardrails "
                "(assessment_id, recorded_at, guardrails_json, prev_hash, row_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (row_id, recorded_at, guardrails_json, prev_hash, row_hash),
            )
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return f"pag_{cur.lastrowid}"

    def close(self) -> None:
        self._con.close()
