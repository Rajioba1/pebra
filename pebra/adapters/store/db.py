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

    def validate_chain(self) -> bool:
        return (
            self._validate_assessment_chain()
            and self._validate_sanction_chain()
            and self._validate_guardrail_chain()
        )

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
