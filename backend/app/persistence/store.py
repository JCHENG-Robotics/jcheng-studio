"""Append-only SQLite event store.

Events are never updated or deleted through the application. The `commands`
table is a current-state projection used for preview/execute continuity and
idempotency diagnostics.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..domain.models import CommandRecord, Event, jsonable
from ..errors import StorageError


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=3000")
        except sqlite3.Error:
            # A read-only or unusual filesystem may reject WAL; keep the
            # connection usable and let writes surface as StorageError.
            pass
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            try:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        command_id TEXT,
                        stage TEXT NOT NULL,
                        wall_time TEXT NOT NULL,
                        monotonic_ms REAL NOT NULL,
                        source TEXT NOT NULL,
                        result TEXT NOT NULL,
                        error_code TEXT,
                        details_json TEXT NOT NULL,
                        schema_version INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_command
                        ON events(command_id);
                    CREATE INDEX IF NOT EXISTS idx_events_wall_time
                        ON events(wall_time);
                    CREATE TABLE IF NOT EXISTS commands (
                        command_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        targets_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        error_code TEXT,
                        details_json TEXT NOT NULL,
                        stage_history_json TEXT NOT NULL,
                        schema_version INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS trajectories (
                        trajectory_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        name TEXT NOT NULL,
                        source TEXT NOT NULL,
                        schema_version INTEGER NOT NULL,
                        dataset_sha256 TEXT NOT NULL UNIQUE,
                        analysis_version TEXT NOT NULL,
                        joint_names_json TEXT NOT NULL,
                        planned_samples INTEGER NOT NULL,
                        commanded_samples INTEGER NOT NULL,
                        feedback_samples INTEGER NOT NULL,
                        feedback_truth TEXT NOT NULL,
                        duration_s REAL NOT NULL,
                        tracking_available INTEGER NOT NULL,
                        dataset_json TEXT NOT NULL,
                        analysis_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_trajectories_created
                        ON trajectories(created_at);
                    CREATE TABLE IF NOT EXISTS experiments (
                        experiment_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        name TEXT NOT NULL,
                        purpose TEXT NOT NULL,
                        data_origin TEXT NOT NULL,
                        operator TEXT NOT NULL,
                        robot_id TEXT NOT NULL,
                        hardware_config TEXT NOT NULL,
                        calibration_version TEXT NOT NULL,
                        mapping_version TEXT NOT NULL,
                        stop_rules TEXT NOT NULL,
                        protocol_json TEXT NOT NULL,
                        plan_json TEXT NOT NULL,
                        notes TEXT NOT NULL,
                        schema_version INTEGER NOT NULL,
                        analysis_version TEXT NOT NULL,
                        signature TEXT UNIQUE
                    );
                    CREATE INDEX IF NOT EXISTS idx_experiments_created
                        ON experiments(created_at);
                    CREATE TABLE IF NOT EXISTS trials (
                        trial_id TEXT PRIMARY KEY,
                        experiment_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        pair_id TEXT NOT NULL,
                        trial_order INTEGER NOT NULL,
                        method TEXT NOT NULL,
                        fixture TEXT NOT NULL,
                        grasp_success INTEGER NOT NULL,
                        continuous_error_mm REAL,
                        rotation_error_deg REAL,
                        failure_code TEXT,
                        failure_detail TEXT NOT NULL,
                        duration_s REAL,
                        measurement_source TEXT NOT NULL,
                        trajectory_id TEXT,
                        artifacts_json TEXT NOT NULL,
                        operator_notes TEXT NOT NULL,
                        target_json TEXT NOT NULL,
                        truth TEXT NOT NULL,
                        schema_version INTEGER NOT NULL,
                        UNIQUE(experiment_id, pair_id, method),
                        UNIQUE(experiment_id, pair_id, trial_order)
                    );
                    CREATE INDEX IF NOT EXISTS idx_trials_experiment
                        ON trials(experiment_id);
                    CREATE TABLE IF NOT EXISTS camera_references (
                        reference_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        source_label TEXT NOT NULL,
                        truth TEXT NOT NULL,
                        camera_topic TEXT NOT NULL,
                        width INTEGER,
                        height INTEGER,
                        encoding TEXT,
                        fps REAL,
                        frame_at TEXT,
                        sample_age_ms INTEGER,
                        calibration_version TEXT NOT NULL,
                        calibration_source TEXT NOT NULL,
                        tf_status TEXT NOT NULL,
                        tf_source TEXT NOT NULL,
                        note TEXT NOT NULL,
                        snapshot_url TEXT NOT NULL,
                        schema_version INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_camera_references_created
                        ON camera_references(created_at);
                    """
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to initialize event store: {exc}") from exc

    # ------------------------------------------------------------------ writes
    def append_event(self, event: Event) -> int:
        with self._lock:
            try:
                cur = self._conn.execute(
                    """
                    INSERT INTO events(event_id, command_id, stage, wall_time,
                                       monotonic_ms, source, result, error_code,
                                       details_json, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.command_id,
                        event.stage,
                        event.wall_time,
                        event.monotonic_ms,
                        event.source,
                        event.result,
                        event.error_code,
                        json.dumps(jsonable(event.details), sort_keys=True),
                        event.schema_version,
                    ),
                )
                self._conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError(f"failed to append event: {exc}") from exc

    def upsert_command(self, record: CommandRecord) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO commands(command_id, session_id, status,
                                         targets_json, created_at, updated_at,
                                         error_code, details_json,
                                         stage_history_json, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(command_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        error_code=excluded.error_code,
                        details_json=excluded.details_json,
                        stage_history_json=excluded.stage_history_json
                    """,
                    (
                        record.command_id,
                        record.session_id,
                        record.status.value,
                        json.dumps(jsonable(record.targets), sort_keys=True),
                        record.created_at,
                        record.updated_at,
                        record.error_code,
                        json.dumps(jsonable(record.details), sort_keys=True),
                        json.dumps(jsonable(record.stage_history), sort_keys=True),
                    ),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError(f"failed to save command: {exc}") from exc

    def insert_trajectory(
        self,
        *,
        trajectory_id: str,
        created_at: str,
        name: str,
        source: str,
        schema_version: int,
        dataset_sha256: str,
        analysis_version: str,
        dataset: dict[str, Any],
        analysis: dict[str, Any],
        summary: dict[str, Any],
    ) -> bool:
        """Insert one dataset. Returns False when the dataset hash already exists."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO trajectories(
                        trajectory_id, created_at, name, source, schema_version,
                        dataset_sha256, analysis_version, joint_names_json,
                        planned_samples, commanded_samples, feedback_samples,
                        feedback_truth, duration_s, tracking_available,
                        dataset_json, analysis_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trajectory_id,
                        created_at,
                        name,
                        source,
                        schema_version,
                        dataset_sha256,
                        analysis_version,
                        json.dumps(summary["joint_names"]),
                        summary["planned_samples"],
                        summary["commanded_samples"],
                        summary["feedback_samples"],
                        summary["feedback_truth"],
                        summary["duration_s"],
                        1 if summary["tracking_available"] else 0,
                        json.dumps(dataset, sort_keys=True),
                        json.dumps(analysis, sort_keys=True),
                    ),
                )
                self._conn.commit()
                return cur.rowcount > 0
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError(f"failed to save trajectory: {exc}") from exc

    @staticmethod
    def _trajectory_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["joint_names"] = json.loads(item.pop("joint_names_json"))
        item["dataset"] = json.loads(item.pop("dataset_json"))
        item["analysis"] = json.loads(item.pop("analysis_json"))
        item["tracking_available"] = bool(item["tracking_available"])
        return item

    @staticmethod
    def _trajectory_summary(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item.pop("dataset_json", None)
        item.pop("analysis_json", None)
        item["joint_names"] = json.loads(item.pop("joint_names_json"))
        item["tracking_available"] = bool(item["tracking_available"])
        return item

    def get_trajectory(self, trajectory_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM trajectories WHERE trajectory_id = ?",
                    (trajectory_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to read trajectory: {exc}") from exc
        return self._trajectory_row(row) if row is not None else None

    def find_trajectory_by_hash(self, digest: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM trajectories WHERE dataset_sha256 = ?",
                    (digest,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to read trajectory: {exc}") from exc
        return self._trajectory_row(row) if row is not None else None

    def list_trajectories(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT trajectory_id, created_at, name, source, schema_version,
                           dataset_sha256, analysis_version, joint_names_json,
                           planned_samples, commanded_samples, feedback_samples,
                           feedback_truth, duration_s, tracking_available
                    FROM trajectories
                    ORDER BY created_at DESC, trajectory_id DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to list trajectories: {exc}") from exc
        return [self._trajectory_summary(row) for row in rows]

    # ------------------------------------------------------ experiments (G3)
    def insert_experiment(
        self,
        *,
        experiment_id: str,
        created_at: str,
        signature: str | None,
        definition: dict[str, Any],
    ) -> bool:
        with self._lock:
            try:
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO experiments(
                        experiment_id, created_at, name, purpose, data_origin,
                        operator, robot_id, hardware_config, calibration_version,
                        mapping_version, stop_rules, protocol_json, plan_json,
                        notes, schema_version, analysis_version, signature
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        experiment_id,
                        created_at,
                        definition["name"],
                        definition["purpose"],
                        definition["data_origin"],
                        definition["operator"],
                        definition["robot_id"],
                        definition["hardware_config"],
                        definition["calibration_version"],
                        definition["mapping_version"],
                        definition["stop_rules"],
                        json.dumps(definition["protocol"], sort_keys=True),
                        json.dumps(definition["plan"], sort_keys=True),
                        definition["notes"],
                        definition["schema_version"],
                        definition["analysis_version"],
                        signature,
                    ),
                )
                self._conn.commit()
                return cur.rowcount > 0
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError(f"failed to save experiment: {exc}") from exc

    @staticmethod
    def _experiment_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["protocol"] = json.loads(item.pop("protocol_json"))
        item["plan"] = json.loads(item.pop("plan_json"))
        return item

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM experiments WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to read experiment: {exc}") from exc
        return self._experiment_row(row) if row is not None else None

    def find_experiment_by_signature(self, signature: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM experiments WHERE signature = ?",
                    (signature,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to read experiment: {exc}") from exc
        return self._experiment_row(row) if row is not None else None

    def list_experiments(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT e.experiment_id, e.created_at, e.name, e.data_origin,
                           e.operator, e.notes, e.protocol_json, e.signature,
                           e.analysis_version,
                           (SELECT COUNT(*) FROM trials t
                             WHERE t.experiment_id = e.experiment_id) AS n_trials,
                           (SELECT COUNT(*) FROM (
                              SELECT pair_id FROM trials t
                              WHERE t.experiment_id = e.experiment_id
                              GROUP BY pair_id
                              HAVING COUNT(DISTINCT method) = 2
                            )) AS n_complete_pairs,
                           (SELECT COUNT(*) FROM trials t
                             WHERE t.experiment_id = e.experiment_id
                               AND t.method = 'baseline') AS n_baseline,
                           (SELECT COUNT(*) FROM trials t
                             WHERE t.experiment_id = e.experiment_id
                               AND t.method = 'intervention') AS n_intervention,
                           (SELECT COUNT(*) FROM trials t
                             WHERE t.experiment_id = e.experiment_id
                               AND t.method = 'baseline'
                               AND t.grasp_success = 1) AS n_baseline_successes,
                           (SELECT COUNT(*) FROM trials t
                             WHERE t.experiment_id = e.experiment_id
                               AND t.method = 'intervention'
                               AND t.grasp_success = 1) AS n_intervention_successes
                    FROM experiments e
                    ORDER BY e.created_at DESC, e.experiment_id DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to list experiments: {exc}") from exc
        result = []
        for row in rows:
            item = dict(row)
            item["protocol"] = json.loads(item.pop("protocol_json"))
            result.append(item)
        return result

    def insert_trial(
        self,
        *,
        trial_id: str,
        experiment_id: str,
        created_at: str,
        trial: dict[str, Any],
    ) -> bool:
        with self._lock:
            try:
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO trials(
                        trial_id, experiment_id, created_at, pair_id, trial_order,
                        method, fixture, grasp_success, continuous_error_mm,
                        rotation_error_deg, failure_code, failure_detail,
                        duration_s, measurement_source, trajectory_id,
                        artifacts_json, operator_notes, target_json, truth,
                        schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trial_id,
                        experiment_id,
                        created_at,
                        trial["pair_id"],
                        trial["trial_order"],
                        trial["method"],
                        trial["fixture"],
                        1 if trial["grasp_success"] else 0,
                        trial["continuous_error_mm"],
                        trial["rotation_error_deg"],
                        trial["failure_code"],
                        trial["failure_detail"],
                        trial["duration_s"],
                        trial["measurement_source"],
                        trial["trajectory_id"],
                        json.dumps(trial["artifacts"], sort_keys=True),
                        trial["operator_notes"],
                        json.dumps(trial["target"], sort_keys=True),
                        trial["truth"],
                        trial["schema_version"],
                    ),
                )
                self._conn.commit()
                return cur.rowcount > 0
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError(f"failed to save trial: {exc}") from exc

    def list_trials(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT * FROM trials
                    WHERE experiment_id = ?
                    ORDER BY pair_id, trial_order, created_at
                    """,
                    (experiment_id,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to list trials: {exc}") from exc
        result = []
        for row in rows:
            item = dict(row)
            item["grasp_success"] = bool(item["grasp_success"])
            item["artifacts"] = json.loads(item.pop("artifacts_json"))
            item["target"] = json.loads(item.pop("target_json"))
            result.append(item)
        return result

    # ----------------------------------------------------------- visual (G4)
    def insert_camera_reference(
        self,
        *,
        reference_id: str,
        created_at: str,
        record: dict[str, Any],
    ) -> bool:
        with self._lock:
            try:
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO camera_references(
                        reference_id, created_at, mode, source_label, truth,
                        camera_topic, width, height, encoding, fps, frame_at,
                        sample_age_ms, calibration_version, calibration_source,
                        tf_status, tf_source, note, snapshot_url, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reference_id,
                        created_at,
                        record["mode"],
                        record["source_label"],
                        record["truth"],
                        record["camera_topic"],
                        record["width"],
                        record["height"],
                        record["encoding"],
                        record["fps"],
                        record["frame_at"],
                        record["sample_age_ms"],
                        record["calibration_version"],
                        record["calibration_source"],
                        record["tf_status"],
                        record["tf_source"],
                        record["note"],
                        record["snapshot_url"],
                        record["schema_version"],
                    ),
                )
                self._conn.commit()
                return cur.rowcount > 0
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError(f"failed to save camera reference: {exc}") from exc

    def list_camera_references(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT * FROM camera_references
                    ORDER BY created_at DESC, reference_id DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to list camera references: {exc}") from exc
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ reads
    def list_events(
        self,
        *,
        limit: int = 200,
        stage: str | None = None,
        source: str | None = None,
        command_id: str | None = None,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["seq > ?"]
        params: list[Any] = [after_seq]
        if stage:
            clauses.append("stage = ?")
            params.append(stage)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if command_id:
            clauses.append("command_id = ?")
            params.append(command_id)
        query = (
            "SELECT seq, event_id, command_id, stage, wall_time, monotonic_ms, "
            "source, result, error_code, details_json, schema_version "
            "FROM events WHERE " + " AND ".join(clauses) + " ORDER BY seq DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 2000)))
        with self._lock:
            try:
                rows = self._conn.execute(query, params).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to read events: {exc}") from exc
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM commands WHERE command_id = ?", (command_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to read command: {exc}") from exc
        if row is None:
            return None
        item = dict(row)
        item["targets"] = json.loads(item.pop("targets_json"))
        item["details"] = json.loads(item.pop("details_json"))
        item["stage_history"] = json.loads(item.pop("stage_history_json"))
        return item

    def export_csv(self) -> str:
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT seq, event_id, command_id, stage, wall_time, monotonic_ms, "
                    "source, result, error_code, details_json, schema_version "
                    "FROM events ORDER BY seq"
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to export events: {exc}") from exc
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "seq",
                "event_id",
                "command_id",
                "stage",
                "wall_time",
                "monotonic_ms",
                "source",
                "result",
                "error_code",
                "details_json",
                "schema_version",
            ]
        )
        for row in rows:
            writer.writerow([row[key] for key in row.keys()])
        return buffer.getvalue()

    def last_seq(self) -> int:
        with self._lock:
            try:
                row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM events").fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"failed to read event sequence: {exc}") from exc
        return int(row["seq"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
