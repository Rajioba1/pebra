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
    row_id: int,
    terminal_status: str,
    detail: dict[str, Any],
    recorded_at: str,
    guidance_packet_id: str | None = None,
    hash_version: int = 2,
) -> str:
    """Per-field canonical content hashed for the outcome chain (Phase 3a / AD-4). Covers the FK,
    terminal status, detail payload, and timestamp so tampering with any of them is detectable."""
    content = {
        "assessment_id": row_id,
        "terminal_status": terminal_status,
        "detail": detail,
        "recorded_at": recorded_at,
        "guidance_packet_id": guidance_packet_id,
        "hash_version": hash_version,
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _outcome_legacy_canonical(
    row_id: int, terminal_status: str, detail: dict[str, Any], recorded_at: str
) -> str:
    content = {
        "assessment_id": row_id,
        "terminal_status": terminal_status,
        "detail": detail,
        "recorded_at": recorded_at,
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _prediction_canonical(
    row_id: int,
    repo_id: str,
    action_id: str | None,
    target_type: str,
    target_name: str,
    predicted_value: float | None,
    prediction_scope: str,
    provenance: dict[str, Any],
    features: dict[str, Any],
    recorded_at: str,
) -> str:
    """v2 per-field canonical for the prediction chain (Phase-4 reframe). Binds ``repo_id`` and
    ``action_id`` (predictions are action-scoped now and M5 promotes scoped facts from them), plus the
    structural ``features`` payload and the ``hash_version`` stamp — so tampering with any of them is
    detectable. ``label_status`` / ``shadow_mode`` stay mutable lifecycle columns (not hashed)."""
    content = {
        "assessment_id": row_id,
        "repo_id": repo_id,
        "action_id": action_id,
        "target_type": target_type,
        "target_name": target_name,
        "predicted_value": predicted_value,
        "prediction_scope": prediction_scope,
        "provenance": provenance,
        "features": features,
        "hash_version": 2,
        "recorded_at": recorded_at,
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _prediction_canonical_v1(
    row_id: int,
    target_type: str,
    target_name: str,
    predicted_value: float | None,
    prediction_scope: str,
    provenance: dict[str, Any],
    recorded_at: str,
) -> str:
    """Legacy (Milestone 4a) prediction canonical — no features, no hash_version. Used only to
    validate pre-Phase-4 rows (hash_version=1)."""
    content = {
        "assessment_id": row_id,
        "target_type": target_type,
        "target_name": target_name,
        "predicted_value": predicted_value,
        "prediction_scope": prediction_scope,
        "provenance": provenance,
        "recorded_at": recorded_at,
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


_PREDICTION_ERROR_FIELDS = (
    "action_id", "target_type", "target_name", "predicted_probability", "predicted_value",
    "actual_outcome", "actual_value", "residual", "brier_error", "log_loss", "squared_error",
    "outcome_label_status", "calibration_scope", "guidance_packet_id",
    "benefit_guidance_influenced", "shadow_mode", "hash_version",
)

_PREDICTION_ERROR_LEGACY_FIELDS = (
    "action_id", "target_type", "target_name", "predicted_probability", "predicted_value",
    "actual_outcome", "actual_value", "residual", "brier_error", "log_loss", "squared_error",
    "outcome_label_status", "calibration_scope",
)


def _prediction_error_canonical(row_id: int, row: dict[str, Any], recorded_at: str) -> str:
    """Per-field canonical for the prediction-error chain (Milestone 4d). Covers the FK, every
    computed field, the label status/scope, and the timestamp so tampering is detectable."""
    content = {"assessment_id": row_id, "recorded_at": recorded_at}
    content.update({f: row.get(f) for f in _PREDICTION_ERROR_FIELDS})
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _prediction_error_legacy_canonical(row_id: int, row: dict[str, Any], recorded_at: str) -> str:
    content = {"assessment_id": row_id, "recorded_at": recorded_at}
    content.update({f: row.get(f) for f in _PREDICTION_ERROR_LEGACY_FIELDS})
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


_RISK_SNAPSHOT_LIFECYCLE_FIELDS = (
    "parent_snapshot_id", "created_from_outcome_hash", "promotion_reason", "rollback_reason",
    "drift_score", "activated_at", "hash_version",
)


def _risk_snapshot_canonical(
    repo_id: str,
    status: str,
    metrics: dict[str, Any],
    created_at: str,
    lifecycle: dict[str, Any] | None = None,
) -> str:
    """Per-field canonical for the (shadow) risk-snapshot chain (Milestone 4d)."""
    content = {"repo_id": repo_id, "status": status, "metrics": metrics, "created_at": created_at}
    lifecycle = lifecycle or {}
    content.update({f: lifecycle.get(f) for f in _RISK_SNAPSHOT_LIFECYCLE_FIELDS})
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _risk_snapshot_legacy_canonical(
    repo_id: str, status: str, metrics: dict[str, Any], created_at: str
) -> str:
    content = {"repo_id": repo_id, "status": status, "metrics": metrics, "created_at": created_at}
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
                guidance_packet_id TEXT,
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
                guidance_packet_id TEXT,
                hash_version INTEGER NOT NULL DEFAULT 2,
                terminal_status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                FOREIGN KEY (assessment_id) REFERENCES assessments(id)
            );
            -- one terminal outcome per assessment (AD-4): the lifecycle closes exactly once
            CREATE UNIQUE INDEX IF NOT EXISTS ux_outcomes_assessment ON outcomes(assessment_id);
            -- Milestone 4a: immutable prediction manifest captured at assess time (WHAT PEBRA
            -- predicted). label_status/shadow_mode are mutable lifecycle columns (not hashed); a
            -- label arrives later when an outcome is recorded. Shadow-only in M4.
            CREATE TABLE IF NOT EXISTS assessment_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id INTEGER NOT NULL,
                repo_id TEXT NOT NULL,
                action_id TEXT,
                target_type TEXT NOT NULL,
                target_name TEXT NOT NULL,
                predicted_value REAL,
                prediction_scope TEXT NOT NULL DEFAULT 'shadow',
                label_status TEXT NOT NULL DEFAULT 'pending',
                shadow_mode INTEGER NOT NULL DEFAULT 1,
                features_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                hash_version INTEGER NOT NULL DEFAULT 2,
                recorded_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                FOREIGN KEY (assessment_id) REFERENCES assessments(id)
            );
            CREATE INDEX IF NOT EXISTS ix_predictions_assessment
                ON assessment_predictions(assessment_id);
            -- Milestone 4d: computed calibration errors (predicted vs actual label). Shadow-only;
            -- never read back into a decision (Hard Rule). Binary targets fill predicted_probability/
            -- actual_outcome/brier_error/log_loss; continuous fill predicted_value/actual_value/
            -- squared_error. outcome_label_status is 'observed' (a real label) or 'censored' (none).
            CREATE TABLE IF NOT EXISTS prediction_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id INTEGER NOT NULL,
                action_id TEXT,
                target_type TEXT NOT NULL,
                target_name TEXT NOT NULL,
                predicted_probability REAL,
                predicted_value REAL,
                actual_outcome INTEGER,
                actual_value REAL,
                residual REAL,
                brier_error REAL,
                log_loss REAL,
                squared_error REAL,
                outcome_label_status TEXT NOT NULL,
                calibration_scope TEXT NOT NULL DEFAULT 'shadow',
                guidance_packet_id TEXT,
                benefit_guidance_influenced INTEGER NOT NULL DEFAULT 0,
                shadow_mode INTEGER NOT NULL DEFAULT 1,
                hash_version INTEGER NOT NULL DEFAULT 2,
                recorded_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                FOREIGN KEY (assessment_id) REFERENCES assessments(id)
            );
            CREATE INDEX IF NOT EXISTS ix_prediction_errors_assessment
                ON prediction_errors(assessment_id);
            -- Milestone 4d: a shadow snapshot per measurement run (the metrics rollup it computed).
            -- status stays 'shadow' in M4; promotion to 'active' is Milestone 5.
            CREATE TABLE IF NOT EXISTS risk_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'shadow',
                metrics_json TEXT NOT NULL DEFAULT '{}',
                parent_snapshot_id TEXT,
                created_from_outcome_hash TEXT,
                promotion_reason TEXT,
                rollback_reason TEXT,
                drift_score REAL,
                activated_at TEXT,
                hash_version INTEGER NOT NULL DEFAULT 2,
                created_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learned_risk_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                snapshot_id TEXT,
                fact_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_name TEXT NOT NULL,
                scope_kind TEXT NOT NULL DEFAULT 'global',
                scope_value TEXT NOT NULL DEFAULT '',
                specificity_rank INTEGER NOT NULL DEFAULT 0,
                scope_json TEXT NOT NULL DEFAULT '{}',
                fact_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'shadow',
                requires_human_ratification INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_learned_risk_facts_repo_status
                ON learned_risk_facts(repo_id, status);
            CREATE INDEX IF NOT EXISTS ix_learned_risk_facts_apply_lookup
                ON learned_risk_facts(
                    repo_id, status, snapshot_id, target_type, target_name,
                    requires_human_ratification, scope_kind, scope_value, specificity_rank
                );
            """
        )
        self._migrate_schema()
        self._create_calibration_views()

    def _migrate_schema(self) -> None:
        self._ensure_column("model_guidance_packets", "guidance_packet_id", "TEXT")
        self._ensure_column("outcomes", "guidance_packet_id", "TEXT")
        # Migration defaults backfill pre-existing rows as v1 legacy chain rows. Every new insert
        # sets hash_version=2 explicitly before hashing, so the DDL default is not used for new rows.
        self._ensure_column("outcomes", "hash_version", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("prediction_errors", "guidance_packet_id", "TEXT")
        self._ensure_column(
            "prediction_errors", "benefit_guidance_influenced", "INTEGER NOT NULL DEFAULT 0"
        )
        self._ensure_column("prediction_errors", "shadow_mode", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("prediction_errors", "hash_version", "INTEGER NOT NULL DEFAULT 1")
        # Phase-4 reframe: pre-existing prediction rows had no features and used the legacy canonical;
        # backfill them as hash_version=1. New rows set hash_version=2 explicitly before hashing.
        # features_json shipped with the table in M4, but ensure it defensively so the v2 INSERT can
        # never hit a missing-column error on any partially-migrated DB.
        self._ensure_column("assessment_predictions", "features_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("assessment_predictions", "hash_version", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("risk_snapshots", "parent_snapshot_id", "TEXT")
        self._ensure_column("risk_snapshots", "created_from_outcome_hash", "TEXT")
        self._ensure_column("risk_snapshots", "promotion_reason", "TEXT")
        self._ensure_column("risk_snapshots", "rollback_reason", "TEXT")
        self._ensure_column("risk_snapshots", "drift_score", "REAL")
        self._ensure_column("risk_snapshots", "activated_at", "TEXT")
        self._ensure_column("risk_snapshots", "hash_version", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("learned_risk_facts", "scope_kind", "TEXT NOT NULL DEFAULT 'global'")
        self._ensure_column("learned_risk_facts", "scope_value", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("learned_risk_facts", "specificity_rank", "INTEGER NOT NULL DEFAULT 0")
        self._con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_guidance_packets_assessment "
            "ON model_guidance_packets(assessment_id)"
        )
        self._con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_guidance_packets_guidance_id "
            "ON model_guidance_packets(guidance_packet_id) "
            "WHERE guidance_packet_id IS NOT NULL"
        )
        self._con.execute(
            "CREATE INDEX IF NOT EXISTS ix_learned_risk_facts_apply_lookup "
            "ON learned_risk_facts("
            "repo_id, status, snapshot_id, target_type, target_name, "
            "requires_human_ratification, scope_kind, scope_value, specificity_rank)"
        )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {
            row[1]
            for row in self._con.execute(f"PRAGMA table_info({table})")
        }
        if column not in cols:
            self._con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _create_calibration_views(self) -> None:
        self._con.executescript(
            """
            CREATE VIEW IF NOT EXISTS risk_binary_calibration_data AS
                SELECT pe.*
                FROM prediction_errors pe
                WHERE pe.target_type = 'risk_binary'
                  AND pe.shadow_mode = 0
                  AND pe.hash_version = 2
                  AND pe.outcome_label_status = 'observed'
                  AND pe.calibration_scope = 'proceeded_edits_only'
                  AND pe.guidance_packet_id IS NULL;
            CREATE VIEW IF NOT EXISTS calibration_data AS
                SELECT * FROM risk_binary_calibration_data;
            CREATE VIEW IF NOT EXISTS benefit_binary_calibration_data AS
                SELECT pe.*
                FROM prediction_errors pe
                WHERE pe.target_type = 'benefit_binary'
                  AND pe.shadow_mode = 0
                  AND pe.hash_version = 2
                  AND pe.outcome_label_status = 'observed'
                  AND pe.calibration_scope = 'proceeded_edits_only'
                  AND pe.guidance_packet_id IS NULL
                  AND pe.benefit_guidance_influenced = 0;
            CREATE VIEW IF NOT EXISTS benefit_continuous_calibration_data AS
                SELECT pe.*
                FROM prediction_errors pe
                WHERE pe.target_type = 'benefit_continuous'
                  AND pe.shadow_mode = 0
                  AND pe.hash_version = 2
                  AND pe.outcome_label_status = 'observed'
                  AND pe.calibration_scope = 'proceeded_edits_only'
                  AND pe.guidance_packet_id IS NULL
                  AND pe.benefit_guidance_influenced = 0;
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

    def _last_prediction_hash(self) -> str:
        row = self._con.execute(
            "SELECT row_hash FROM assessment_predictions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def persist_assessment(
        self,
        result: AssessmentResult,
        request_payload: dict[str, Any],
        predictions: list[dict[str, Any]] | None = None,
    ) -> str:
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            self._con.execute("BEGIN IMMEDIATE")
            assessment_id = self._next_assessment_row_id()
            packet = None
            guidance_packet_id = None
            if result.model_guidance_packet is not None:
                packet = dict(result.model_guidance_packet)
                guidance_packet_id = self._guidance_packet_uid(assessment_id, packet)
                packet["guidance_packet_id"] = guidance_packet_id
                result.model_guidance_packet = packet
            content_json = _canonical(result, request_payload)
            prev_hash = self._last_assessment_hash()
            row_hash = _row_hash(prev_hash, content_json)
            cur = self._con.execute(
                "INSERT INTO assessments (repo_id, decision, content_json, prev_hash, row_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (result.repo_id, result.recommended_decision.value, content_json, prev_hash, row_hash),
            )
            assessment_id = cur.lastrowid
            if packet is not None:
                self._con.execute(
                    "INSERT INTO model_guidance_packets "
                    "(assessment_id, guidance_packet_id, packet_json) VALUES (?, ?, ?)",
                    (
                        assessment_id,
                        guidance_packet_id,
                        json.dumps(packet, sort_keys=True),
                    ),
                )
            # Milestone 4a: capture the prediction manifest atomically with the assessment so the two
            # can never diverge. Each row extends the prediction hash chain.
            for pred in predictions or ():
                self._insert_prediction(assessment_id, result.repo_id, pred, recorded_at)
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return f"asm_{assessment_id}"

    def _next_assessment_row_id(self) -> int:
        row = self._con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'assessments'"
        ).fetchone()
        if row is not None:
            return int(row[0]) + 1
        row = self._con.execute("SELECT COALESCE(MAX(id), 0) FROM assessments").fetchone()
        return int(row[0]) + 1

    @staticmethod
    def _guidance_packet_uid(row_id: int, packet: dict[str, Any]) -> str:
        logical = str(packet.get("guidance_packet_id") or "packet")
        return f"gp_{row_id}_{logical}"

    def guidance_packet_id_for_assessment(self, assessment_id: str) -> str | None:
        row_id = self._row_id(assessment_id)
        row = self._con.execute(
            "SELECT guidance_packet_id FROM model_guidance_packets WHERE assessment_id = ?",
            (row_id,),
        ).fetchone()
        return row[0] if row else None

    def _insert_prediction(
        self, assessment_id: int, repo_id: str, pred: dict[str, Any], recorded_at: str
    ) -> None:
        """Append one prediction row to the chain. Caller owns the surrounding transaction."""
        provenance = pred.get("provenance") or {}
        predicted_value = pred.get("predicted_value")
        scope = pred.get("prediction_scope", "shadow")
        features = pred.get("features") or {}
        action_id = pred.get("action_id")
        content_json = _prediction_canonical(
            assessment_id, repo_id, action_id, pred["target_type"], pred["target_name"],
            predicted_value, scope, provenance, features, recorded_at,
        )
        prev_hash = self._last_prediction_hash()
        row_hash = _row_hash(prev_hash, content_json)
        self._con.execute(
            "INSERT INTO assessment_predictions "
            "(assessment_id, repo_id, action_id, target_type, target_name, predicted_value, "
            " prediction_scope, label_status, shadow_mode, features_json, provenance_json, "
            " hash_version, recorded_at, prev_hash, row_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, 2, ?, ?, ?)",
            (
                assessment_id, repo_id, pred.get("action_id"), pred["target_type"],
                pred["target_name"], predicted_value, scope,
                json.dumps(features, sort_keys=True),
                json.dumps(provenance, sort_keys=True), recorded_at, prev_hash, row_hash,
            ),
        )

    def load_predictions(self, assessment_id: str) -> list[dict[str, Any]]:
        """The captured prediction manifest for an assessment (Milestone 4 join source)."""
        row_id = self._row_id(assessment_id)
        return [
            {
                "prediction_id": f"ap_{pid}",
                "action_id": action_id,
                "target_type": target_type,
                "target_name": target_name,
                "predicted_value": predicted_value,
                "prediction_scope": scope,
                "label_status": label_status,
                "shadow_mode": shadow_mode,
                "provenance": json.loads(provenance_json),
                "features": json.loads(features_json),
            }
            for pid, action_id, target_type, target_name, predicted_value, scope, label_status,
            shadow_mode, provenance_json, features_json in self._con.execute(
                "SELECT id, action_id, target_type, target_name, predicted_value, prediction_scope, "
                "label_status, shadow_mode, provenance_json, features_json FROM assessment_predictions "
                "WHERE assessment_id = ? ORDER BY id ASC",
                (row_id,),
            )
        ]

    def read_active_snapshot_rows(self, repo_id: str) -> dict[str, Any] | None:
        """M5c read-path (read-only): the newest ACTIVE risk_snapshot for a repo + its ACTIVE,
        human-ratified learned facts as raw rows. Returns None when no active snapshot exists. The
        snapshot_read adapter decodes fact_json/scope_json and applies the min-sample policy; this
        method only issues SELECTs. learned_risk_facts.snapshot_id is the bare integer-as-text, e.g.
        "1", matching risk_snapshots.id; the "rs_{id}" display form is also accepted defensively."""
        snap = self._con.execute(
            "SELECT id FROM risk_snapshots WHERE repo_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
        if snap is None:
            return None
        rs_id = snap[0]
        rows = self._con.execute(
            "SELECT id, target_type, target_name, scope_kind, scope_value, specificity_rank, "
            "scope_json, fact_json, created_at FROM learned_risk_facts "
            "WHERE repo_id = ? AND snapshot_id IN (?, ?) AND status = 'active' "
            "AND requires_human_ratification = 0 AND fact_type = 'learned_override' "
            "ORDER BY id ASC",
            # canonical is the bare integer-as-text ("1"); also accept the "rs_{id}" display form so a
            # future M5d writer that stores insert_risk_snapshot's return value can't silently break.
            (repo_id, str(rs_id), f"rs_{rs_id}"),
        ).fetchall()
        return {
            "snapshot_id": f"rs_{rs_id}",
            "facts": [
                {
                    "fact_id": f"lrf_{fid}",
                    "target_type": tt,
                    "target_name": tn,
                    "scope_kind": sk,
                    "scope_value": sv,
                    "specificity_rank": rank,
                    "scope_json": scope_json,
                    "fact_json": fact_json,
                    "created_at": created_at,
                }
                for (fid, tt, tn, sk, sv, rank, scope_json, fact_json, created_at) in rows
            ],
        }

    # --- Milestone 4d: computed prediction errors + shadow snapshots (the learning store writes here)

    def _last_prediction_error_hash(self) -> str:
        row = self._con.execute(
            "SELECT row_hash FROM prediction_errors ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def _last_risk_snapshot_hash(self) -> str:
        row = self._con.execute(
            "SELECT row_hash FROM risk_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def insert_prediction_error(self, assessment_id: str, row: dict[str, Any]) -> str:
        """Append one computed prediction-error row (hash-chained). Returns ``pe_{id}``."""
        row_id = self._row_id(assessment_id)
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # normalize defaults BEFORE hashing so the canonical matches the stored columns exactly
        row = {**row, "calibration_scope": row.get("calibration_scope", "shadow")}
        try:
            self._con.execute("BEGIN IMMEDIATE")
            if self._con.execute("SELECT 1 FROM assessments WHERE id = ?", (row_id,)).fetchone() is None:
                raise KeyError(f"no assessment {assessment_id!r}")
            pe_id = self._insert_prediction_error_for_row_id(row_id, row, recorded_at)
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return pe_id

    def _insert_prediction_error_for_row_id(
        self, row_id: int, row: dict[str, Any], recorded_at: str
    ) -> str:
        """Append one computed prediction-error row. Caller may own an outer transaction."""
        row = {
            **row,
            "guidance_packet_id": row.get("guidance_packet_id"),
            "benefit_guidance_influenced": int(bool(row.get("benefit_guidance_influenced", False))),
            "shadow_mode": int(row.get("shadow_mode", 1)),
            "hash_version": int(row.get("hash_version", 2)),
        }
        content_json = _prediction_error_canonical(row_id, row, recorded_at)
        prev_hash = self._last_prediction_error_hash()
        row_hash = _row_hash(prev_hash, content_json)
        cur = self._con.execute(
            "INSERT INTO prediction_errors "
            "(assessment_id, action_id, target_type, target_name, predicted_probability, "
            " predicted_value, actual_outcome, actual_value, residual, brier_error, log_loss, "
            " squared_error, outcome_label_status, calibration_scope, guidance_packet_id, "
            " benefit_guidance_influenced, shadow_mode, hash_version, recorded_at, "
            " prev_hash, row_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id, row.get("action_id"), row["target_type"], row["target_name"],
                row.get("predicted_probability"), row.get("predicted_value"),
                row.get("actual_outcome"), row.get("actual_value"), row.get("residual"),
                row.get("brier_error"), row.get("log_loss"), row.get("squared_error"),
                row["outcome_label_status"], row.get("calibration_scope", "shadow"),
                row.get("guidance_packet_id"), row["benefit_guidance_influenced"],
                row["shadow_mode"], row["hash_version"], recorded_at, prev_hash, row_hash,
            ),
        )
        return f"pe_{cur.lastrowid}"

    def insert_risk_snapshot(self, repo_id: str, metrics: dict[str, Any], status: str = "shadow") -> str:
        """Append a (shadow) risk snapshot recording a measurement run's metrics. Returns ``rs_{id}``."""
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            self._con.execute("BEGIN IMMEDIATE")
            snapshot_id = self._insert_risk_snapshot(repo_id, metrics, status, created_at)
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return snapshot_id

    def _insert_risk_snapshot(
        self, repo_id: str, metrics: dict[str, Any], status: str, created_at: str
    ) -> str:
        """Append one risk-snapshot row. Caller may own an outer transaction."""
        metrics_json = json.dumps(metrics, sort_keys=True, separators=(",", ":"))
        lifecycle = {f: metrics.get(f) for f in _RISK_SNAPSHOT_LIFECYCLE_FIELDS}
        lifecycle["hash_version"] = int(lifecycle.get("hash_version") or 2)
        content_json = _risk_snapshot_canonical(repo_id, status, metrics, created_at, lifecycle)
        prev_hash = self._last_risk_snapshot_hash()
        row_hash = _row_hash(prev_hash, content_json)
        cur = self._con.execute(
            "INSERT INTO risk_snapshots "
            "(repo_id, status, metrics_json, parent_snapshot_id, created_from_outcome_hash, "
            "promotion_reason, rollback_reason, drift_score, activated_at, hash_version, "
            "created_at, prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                repo_id, status, metrics_json, lifecycle.get("parent_snapshot_id"),
                lifecycle.get("created_from_outcome_hash"), lifecycle.get("promotion_reason"),
                lifecycle.get("rollback_reason"), lifecycle.get("drift_score"),
                lifecycle.get("activated_at"), lifecycle["hash_version"], created_at,
                prev_hash, row_hash,
            ),
        )
        return f"rs_{cur.lastrowid}"

    def insert_learning_measurement(
        self, assessment_id: str, rows: list[dict[str, Any]],
        repo_id: str, metrics: dict[str, Any], status: str = "shadow",
    ) -> tuple[list[str], str]:
        """Atomically append all prediction-error rows plus the shadow snapshot for one measurement.

        A measurement run is one logical unit. If any row or the snapshot fails, the whole run rolls
        back so ``prediction_errors_exist`` cannot trap the assessment in a partial measured state.
        """
        row_id = self._row_id(assessment_id)
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        normalized = [{**row, "calibration_scope": row.get("calibration_scope", "shadow")} for row in rows]
        try:
            self._con.execute("BEGIN IMMEDIATE")
            if self._con.execute("SELECT 1 FROM assessments WHERE id = ?", (row_id,)).fetchone() is None:
                raise KeyError(f"no assessment {assessment_id!r}")
            error_ids = [
                self._insert_prediction_error_for_row_id(row_id, row, recorded_at)
                for row in normalized
            ]
            snapshot_id = self._insert_risk_snapshot(repo_id, metrics, status, created_at)
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return error_ids, snapshot_id

    def prediction_errors_exist(self, assessment_id: str) -> bool:
        """True iff this assessment already has computed prediction-error rows (idempotency guard:
        re-measuring would double-count in the scorecard)."""
        row_id = self._row_id(assessment_id)
        return self._con.execute(
            "SELECT 1 FROM prediction_errors WHERE assessment_id = ? LIMIT 1", (row_id,)
        ).fetchone() is not None

    def load_prediction_errors(self, repo_id: str | None = None) -> list[dict[str, Any]]:
        """All computed prediction errors (optionally repo-scoped via the assessment join), for the
        scorecard. Returns plain dicts; aggregation lives in the surface."""
        sql = (
            "SELECT pe.target_type, pe.target_name, pe.predicted_probability, pe.predicted_value, "
            "pe.actual_outcome, pe.actual_value, pe.brier_error, pe.log_loss, pe.squared_error, "
            "pe.outcome_label_status, pe.calibration_scope, pe.guidance_packet_id, "
            "pe.benefit_guidance_influenced, pe.shadow_mode, pe.assessment_id "
            "FROM prediction_errors pe JOIN assessments a ON a.id = pe.assessment_id "
        )
        params: tuple = ()
        if repo_id is not None:
            sql += "WHERE a.repo_id = ? "
            params = (repo_id,)
        sql += "ORDER BY pe.id ASC"
        cols = (
            "target_type", "target_name", "predicted_probability", "predicted_value",
            "actual_outcome", "actual_value", "brier_error", "log_loss", "squared_error",
            "outcome_label_status", "calibration_scope", "guidance_packet_id",
            "benefit_guidance_influenced", "shadow_mode", "assessment_id",
        )
        return [dict(zip(cols, r)) for r in self._con.execute(sql, params)]

    def load_production_calibration_rows(
        self, repo_id: str | None = None, target_type: str = "risk_binary"
    ) -> list[dict[str, Any]]:
        """Rows from the filtered production calibration views (M5 promotion input).

        This intentionally differs from ``load_prediction_errors``: the scorecard can inspect shadow
        rows, but promotion must read only live, observed, proceeded, unguided labels.
        """
        views = {
            "risk_binary": "risk_binary_calibration_data",
            "benefit_binary": "benefit_binary_calibration_data",
            "benefit_continuous": "benefit_continuous_calibration_data",
        }
        view = views[target_type]
        sql = f"SELECT v.* FROM {view} v JOIN assessments a ON a.id = v.assessment_id "
        params: tuple = ()
        if repo_id is not None:
            sql += "WHERE a.repo_id = ? "
            params = (repo_id,)
        sql += "ORDER BY v.id ASC"
        rows = self._con.execute(sql, params)
        cols = [d[0] for d in rows.description]
        return [dict(zip(cols, r)) for r in rows.fetchall()]

    def record_outcome(self, assessment_id: str, status: str, detail: dict | None = None) -> None:
        """Append the terminal outcome of an assessed action (AD-4). OutcomePort impl: the assessment
        row stays immutable; this is the only terminal-status write. Its own append-only hash chain
        mirrors the guardrail chain. Raises KeyError if the assessment doesn't exist."""
        row_id = self._row_id(assessment_id)
        payload = detail or {}
        guidance_packet_id = self.guidance_packet_id_for_assessment(assessment_id)
        hash_version = 2
        detail_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        content_json = _outcome_canonical(
            row_id, status, payload, recorded_at, guidance_packet_id, hash_version
        )
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
                "(assessment_id, guidance_packet_id, hash_version, terminal_status, detail_json, "
                "recorded_at, prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id, guidance_packet_id, hash_version, status, detail_json,
                    recorded_at, prev_hash, row_hash,
                ),
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
            for table in (
                "assessments", "sanction_events", "post_assessment_guardrails", "outcomes",
                "assessment_predictions", "prediction_errors", "risk_snapshots",
                "learned_risk_facts",
            )
        }
        return {"valid": self.validate_chain(), "counts": counts}

    def validate_chain(self) -> bool:
        return (
            self._validate_assessment_chain()
            and self._validate_sanction_chain()
            and self._validate_guardrail_chain()
            and self._validate_outcome_chain()
            and self._validate_prediction_chain()
            and self._validate_prediction_error_chain()
            and self._validate_risk_snapshot_chain()
            and self._validate_learned_risk_fact_chain()
        )

    def validate_learning_chains(self) -> bool:
        """Return True iff the snapshot/fact chains trusted by the live read path are intact."""
        return self._validate_risk_snapshot_chain() and self._validate_learned_risk_fact_chain()

    def _validate_learned_risk_fact_chain(self) -> bool:
        prev_hash = GENESIS
        for (
            repo_id, snapshot_id, fact_type, target_type, target_name, scope_kind, scope_value,
            specificity_rank, scope_json, fact_json, status, requires_human_ratification,
            created_at, stored_prev, stored_hash,
        ) in self._con.execute(
            "SELECT repo_id, snapshot_id, fact_type, target_type, target_name, scope_kind, "
            "scope_value, specificity_rank, scope_json, fact_json, status, "
            "requires_human_ratification, created_at, prev_hash, row_hash "
            "FROM learned_risk_facts ORDER BY id ASC"
        ):
            if stored_prev != prev_hash:
                return False
            try:
                scope = json.loads(scope_json)
                fact = json.loads(fact_json)
            except (TypeError, ValueError):
                return False
            content_json = json.dumps(
                {
                    "repo_id": repo_id,
                    "snapshot_id": snapshot_id,
                    "fact_type": fact_type,
                    "target_type": target_type,
                    "target_name": target_name,
                    "scope_kind": scope_kind,
                    "scope_value": scope_value,
                    "specificity_rank": specificity_rank,
                    "scope": scope,
                    "fact": fact,
                    "status": status,
                    "requires_human_ratification": requires_human_ratification,
                    "created_at": created_at,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def _validate_prediction_error_chain(self) -> bool:
        prev_hash = GENESIS
        for row in self._con.execute(
            "SELECT assessment_id, action_id, target_type, target_name, predicted_probability, "
            "predicted_value, actual_outcome, actual_value, residual, brier_error, log_loss, "
            "squared_error, outcome_label_status, calibration_scope, guidance_packet_id, "
            "benefit_guidance_influenced, shadow_mode, hash_version, recorded_at, prev_hash, row_hash "
            "FROM prediction_errors ORDER BY id ASC"
        ):
            (assessment_id, *fields, recorded_at, stored_prev, stored_hash) = row
            if stored_prev != prev_hash:
                return False
            content = dict(zip(_PREDICTION_ERROR_FIELDS, fields))
            if content.get("hash_version") == 2:
                content_json = _prediction_error_canonical(assessment_id, content, recorded_at)
            else:
                content_json = _prediction_error_legacy_canonical(assessment_id, content, recorded_at)
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def _validate_risk_snapshot_chain(self) -> bool:
        prev_hash = GENESIS
        for row in self._con.execute(
            "SELECT repo_id, status, metrics_json, parent_snapshot_id, created_from_outcome_hash, "
            "promotion_reason, rollback_reason, drift_score, activated_at, hash_version, created_at, "
            "prev_hash, row_hash "
            "FROM risk_snapshots ORDER BY id ASC"
        ):
            (
                repo_id, status, metrics_json, parent_snapshot_id, created_from_outcome_hash,
                promotion_reason, rollback_reason, drift_score, activated_at, hash_version, created_at,
                stored_prev, stored_hash,
            ) = row
            if stored_prev != prev_hash:
                return False
            if hash_version == 2:
                content_json = _risk_snapshot_canonical(
                    repo_id, status, json.loads(metrics_json), created_at,
                    {
                        "parent_snapshot_id": parent_snapshot_id,
                        "created_from_outcome_hash": created_from_outcome_hash,
                        "promotion_reason": promotion_reason,
                        "rollback_reason": rollback_reason,
                        "drift_score": drift_score,
                        "activated_at": activated_at,
                        "hash_version": hash_version,
                    },
                )
            else:
                content_json = _risk_snapshot_legacy_canonical(
                    repo_id, status, json.loads(metrics_json), created_at
                )
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def _validate_prediction_chain(self) -> bool:
        prev_hash = GENESIS
        for (
            assessment_id, repo_id, action_id, target_type, target_name, predicted_value, scope,
            provenance_json, features_json, hash_version, recorded_at, stored_prev, stored_hash,
        ) in self._con.execute(
            "SELECT assessment_id, repo_id, action_id, target_type, target_name, predicted_value, "
            "prediction_scope, provenance_json, features_json, hash_version, recorded_at, "
            "prev_hash, row_hash FROM assessment_predictions ORDER BY id ASC"
        ):
            if stored_prev != prev_hash:
                return False
            provenance = json.loads(provenance_json)
            if hash_version == 2:
                content_json = _prediction_canonical(
                    assessment_id, repo_id, action_id, target_type, target_name, predicted_value,
                    scope, provenance, json.loads(features_json), recorded_at,
                )
            else:
                content_json = _prediction_canonical_v1(
                    assessment_id, target_type, target_name, predicted_value, scope,
                    provenance, recorded_at,
                )
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def _validate_outcome_chain(self) -> bool:
        prev_hash = GENESIS
        for (
            assessment_id, guidance_packet_id, hash_version, terminal_status, detail_json,
            recorded_at, stored_prev, stored_hash
        ) in (
            self._con.execute(
                "SELECT assessment_id, guidance_packet_id, hash_version, terminal_status, detail_json, "
                "recorded_at, prev_hash, row_hash "
                "FROM outcomes ORDER BY id ASC"
            )
        ):
            if stored_prev != prev_hash:
                return False
            if hash_version == 2:
                content_json = _outcome_canonical(
                    assessment_id, terminal_status, json.loads(detail_json), recorded_at,
                    guidance_packet_id, hash_version
                )
            else:
                content_json = _outcome_legacy_canonical(
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

    def active_sanction_for_action(self, repo_id: str, action_id: str) -> dict[str, Any] | None:
        """Return the newest active sanction whose risk profile is explicitly bound to this action.

        A bare/string risk_profile is intentionally not reusable for assess-side conversion: the
        sanction must name the action it authorizes, either at top level or inside risk_profile.
        """
        try:
            self._con.execute("BEGIN IMMEDIATE")
            rows = self._con.execute(
                "SELECT sanction_json FROM sanction_events "
                "WHERE repo_id = ? AND status = 'active' ORDER BY id DESC",
                (repo_id,),
            ).fetchall()
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        for (raw,) in rows:
            sanction = json.loads(raw)
            profile = sanction.get("risk_profile")
            action_ids: set[str] = set()
            if isinstance(profile, dict):
                value = profile.get("action_id")
                if isinstance(value, str):
                    action_ids.add(value)
                value = profile.get("action_ids")
                if isinstance(value, list):
                    action_ids.update(str(v) for v in value)
            value = sanction.get("action_id")
            if isinstance(value, str):
                action_ids.add(value)
            value = sanction.get("action_ids")
            if isinstance(value, list):
                action_ids.update(str(v) for v in value)
            if action_id in action_ids:
                return sanction
        return None

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

    def latest_guardrails(self, assessment_id: str) -> dict[str, Any] | None:
        row_id = self._row_id(assessment_id)
        if self._con.execute("SELECT 1 FROM assessments WHERE id = ?", (row_id,)).fetchone() is None:
            raise KeyError(f"no assessment {assessment_id!r}")
        row = self._con.execute(
            "SELECT guardrails_json FROM post_assessment_guardrails "
            "WHERE assessment_id = ? ORDER BY id DESC LIMIT 1",
            (row_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

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
