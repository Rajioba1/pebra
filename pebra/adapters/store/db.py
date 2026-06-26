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


def _prediction_canonical(
    row_id: int,
    target_type: str,
    target_name: str,
    predicted_value: float | None,
    prediction_scope: str,
    provenance: dict[str, Any],
    recorded_at: str,
) -> str:
    """Per-field canonical content hashed for the prediction chain (Milestone 4a). Covers the FK,
    the target identity, the predicted value, scope, provenance and timestamp so tampering with any
    of them is detectable. ``label_status`` / ``shadow_mode`` are mutable lifecycle columns (a label
    arrives later via the outcome) and are intentionally NOT hashed, like sanction status."""
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
    "outcome_label_status", "calibration_scope",
)


def _prediction_error_canonical(row_id: int, row: dict[str, Any], recorded_at: str) -> str:
    """Per-field canonical for the prediction-error chain (Milestone 4d). Covers the FK, every
    computed field, the label status/scope, and the timestamp so tampering is detectable."""
    content = {"assessment_id": row_id, "recorded_at": recorded_at}
    content.update({f: row.get(f) for f in _PREDICTION_ERROR_FIELDS})
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _risk_snapshot_canonical(repo_id: str, status: str, metrics: dict[str, Any], created_at: str) -> str:
    """Per-field canonical for the (shadow) risk-snapshot chain (Milestone 4d)."""
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
                created_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL
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
        content_json = _canonical(result, request_payload)
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
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
            # Milestone 4a: capture the prediction manifest atomically with the assessment so the two
            # can never diverge. Each row extends the prediction hash chain.
            for pred in predictions or ():
                self._insert_prediction(assessment_id, result.repo_id, pred, recorded_at)
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return f"asm_{assessment_id}"

    def _insert_prediction(
        self, assessment_id: int, repo_id: str, pred: dict[str, Any], recorded_at: str
    ) -> None:
        """Append one prediction row to the chain. Caller owns the surrounding transaction."""
        provenance = pred.get("provenance") or {}
        predicted_value = pred.get("predicted_value")
        scope = pred.get("prediction_scope", "shadow")
        content_json = _prediction_canonical(
            assessment_id, pred["target_type"], pred["target_name"],
            predicted_value, scope, provenance, recorded_at,
        )
        prev_hash = self._last_prediction_hash()
        row_hash = _row_hash(prev_hash, content_json)
        self._con.execute(
            "INSERT INTO assessment_predictions "
            "(assessment_id, repo_id, action_id, target_type, target_name, predicted_value, "
            " prediction_scope, label_status, shadow_mode, features_json, provenance_json, "
            " recorded_at, prev_hash, row_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?, ?)",
            (
                assessment_id, repo_id, pred.get("action_id"), pred["target_type"],
                pred["target_name"], predicted_value, scope,
                json.dumps(pred.get("features") or {}, sort_keys=True),
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
            }
            for pid, action_id, target_type, target_name, predicted_value, scope, label_status,
            shadow_mode, provenance_json in self._con.execute(
                "SELECT id, action_id, target_type, target_name, predicted_value, prediction_scope, "
                "label_status, shadow_mode, provenance_json FROM assessment_predictions "
                "WHERE assessment_id = ? ORDER BY id ASC",
                (row_id,),
            )
        ]

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
        content_json = _prediction_error_canonical(row_id, row, recorded_at)
        prev_hash = self._last_prediction_error_hash()
        row_hash = _row_hash(prev_hash, content_json)
        cur = self._con.execute(
            "INSERT INTO prediction_errors "
            "(assessment_id, action_id, target_type, target_name, predicted_probability, "
            " predicted_value, actual_outcome, actual_value, residual, brier_error, log_loss, "
            " squared_error, outcome_label_status, calibration_scope, recorded_at, "
            " prev_hash, row_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id, row.get("action_id"), row["target_type"], row["target_name"],
                row.get("predicted_probability"), row.get("predicted_value"),
                row.get("actual_outcome"), row.get("actual_value"), row.get("residual"),
                row.get("brier_error"), row.get("log_loss"), row.get("squared_error"),
                row["outcome_label_status"], row.get("calibration_scope", "shadow"),
                recorded_at, prev_hash, row_hash,
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
        content_json = _risk_snapshot_canonical(repo_id, status, metrics, created_at)
        prev_hash = self._last_risk_snapshot_hash()
        row_hash = _row_hash(prev_hash, content_json)
        cur = self._con.execute(
            "INSERT INTO risk_snapshots (repo_id, status, metrics_json, created_at, "
            "prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?)",
            (repo_id, status, metrics_json, created_at, prev_hash, row_hash),
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
            "pe.outcome_label_status, pe.calibration_scope, pe.assessment_id "
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
            "outcome_label_status", "calibration_scope", "assessment_id",
        )
        return [dict(zip(cols, r)) for r in self._con.execute(sql, params)]

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
            for table in (
                "assessments", "sanction_events", "post_assessment_guardrails", "outcomes",
                "assessment_predictions", "prediction_errors", "risk_snapshots",
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
        )

    def _validate_prediction_error_chain(self) -> bool:
        prev_hash = GENESIS
        for row in self._con.execute(
            "SELECT assessment_id, action_id, target_type, target_name, predicted_probability, "
            "predicted_value, actual_outcome, actual_value, residual, brier_error, log_loss, "
            "squared_error, outcome_label_status, calibration_scope, recorded_at, prev_hash, row_hash "
            "FROM prediction_errors ORDER BY id ASC"
        ):
            (assessment_id, *fields, recorded_at, stored_prev, stored_hash) = row
            if stored_prev != prev_hash:
                return False
            content = dict(zip(_PREDICTION_ERROR_FIELDS, fields))
            content_json = _prediction_error_canonical(assessment_id, content, recorded_at)
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def _validate_risk_snapshot_chain(self) -> bool:
        prev_hash = GENESIS
        for repo_id, status, metrics_json, created_at, stored_prev, stored_hash in self._con.execute(
            "SELECT repo_id, status, metrics_json, created_at, prev_hash, row_hash "
            "FROM risk_snapshots ORDER BY id ASC"
        ):
            if stored_prev != prev_hash:
                return False
            content_json = _risk_snapshot_canonical(
                repo_id, status, json.loads(metrics_json), created_at
            )
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def _validate_prediction_chain(self) -> bool:
        prev_hash = GENESIS
        for (
            assessment_id, target_type, target_name, predicted_value, scope, provenance_json,
            recorded_at, stored_prev, stored_hash,
        ) in self._con.execute(
            "SELECT assessment_id, target_type, target_name, predicted_value, prediction_scope, "
            "provenance_json, recorded_at, prev_hash, row_hash "
            "FROM assessment_predictions ORDER BY id ASC"
        ):
            if stored_prev != prev_hash:
                return False
            content_json = _prediction_canonical(
                assessment_id, target_type, target_name, predicted_value, scope,
                json.loads(provenance_json), recorded_at,
            )
            if _row_hash(prev_hash, content_json) != stored_hash:
                return False
            prev_hash = stored_hash
        return True

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
