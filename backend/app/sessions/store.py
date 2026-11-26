"""SQLite operator-session store (M2).

The store is append-only for events. The `sessions` row is a materialized
current state; a session is sealed by writing a SHA-256 over the final record
and its ordered events. The database and exports live outside the repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..domain.models import utc_now_iso

SCHEMA_VERSION = 1
ACTIVE_STATES = {"open", "authorized", "degraded"}
EVENT_TYPES = {
    "session_opened",
    "authorize_requested",
    "authorize_rejected",
    "authorized",
    "degraded",
    "closed",
    "exported",
    "note",
}


class SessionStoreError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def sanitize_gateway_url(value: str | None) -> str | None:
    """Keep scheme/host/port/path; drop userinfo, query and fragment."""
    if not value:
        return None
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        return value.strip().split("?", 1)[0].split("#", 1)[0]
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class SessionStore:
    def __init__(
        self,
        db_path: Path,
        export_dir: Path,
        *,
        studio_version: str,
        studio_commit: str | None,
        robot_id: str,
        gateway_url: str | None,
        calibration: dict[str, Any],
    ) -> None:
        self.db_path = Path(db_path)
        self.export_dir = Path(export_dir)
        self.studio_version = studio_version
        self.studio_commit = studio_commit
        self.robot_id = robot_id
        self.gateway_url = sanitize_gateway_url(gateway_url)
        self.calibration = dict(calibration)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------- connection
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.export_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._create_schema(conn)
            self._conn = conn
        return self._conn

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                operator TEXT NOT NULL,
                purpose TEXT NOT NULL,
                studio_version TEXT NOT NULL,
                studio_commit TEXT,
                gateway_url TEXT,
                contract_hash TEXT,
                robot_profile TEXT,
                calibration_json TEXT NOT NULL,
                state TEXT NOT NULL,
                close_reason TEXT,
                record_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS session_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                at TEXT NOT NULL,
                type TEXT NOT NULL,
                provenance TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(session_id, seq),
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            );
            CREATE INDEX IF NOT EXISTS session_events_session
                ON session_events(session_id, seq);
            """
        )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------ open
    def open_session(
        self,
        *,
        operator: str,
        purpose: str,
        robot_id: str | None = None,
    ) -> dict[str, Any]:
        operator = operator.strip()
        purpose = purpose.strip()
        if not operator or len(operator) > 128:
            raise SessionStoreError("SESSION_INVALID", "operator must be 1..128 chars", status_code=400)
        if not purpose or len(purpose) > 500:
            raise SessionStoreError("SESSION_INVALID", "purpose must be 1..500 chars", status_code=400)
        with self._lock:
            conn = self._connection()
            with conn:
                active = self._active_row(conn)
                if active is not None:
                    raise SessionStoreError(
                        "SESSION_CONFLICT",
                        f"session {active['session_id']} is still active",
                    )
                session_id = f"sess-{uuid.uuid4().hex[:12]}"
                opened_at = utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO sessions (
                        session_id, schema_version, opened_at, operator, purpose,
                        studio_version, studio_commit, gateway_url, robot_profile,
                        calibration_json, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        session_id,
                        SCHEMA_VERSION,
                        opened_at,
                        operator,
                        purpose,
                        self.studio_version,
                        self.studio_commit,
                        self.gateway_url,
                        (robot_id or self.robot_id).strip(),
                        _canonical(self.calibration),
                    ),
                )
                self._append_event(
                    conn,
                    session_id,
                    "session_opened",
                    "operator",
                    {"operator": operator, "purpose": purpose, "robot_id": robot_id or self.robot_id},
                )
            return self.get(session_id)

    # ------------------------------------------------------------- transition
    def authorize(
        self,
        session_id: str,
        *,
        contract_hash: str | None,
        profile_hash: str | None,
        gateway_status: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._connection()
            with conn:
                row = self._row(conn, session_id)
                self._require_active(row)
                conn.execute(
                    """
                    UPDATE sessions
                    SET state = 'authorized',
                        contract_hash = ?,
                        robot_profile = COALESCE(?, robot_profile)
                    WHERE session_id = ?
                    """,
                    (contract_hash, profile_hash, session_id),
                )
                self._append_event(
                    conn,
                    session_id,
                    "authorized",
                    "gateway",
                    {
                        "contract_hash": contract_hash,
                        "profile_hash": profile_hash,
                        "gateway_status": gateway_status,
                    },
                )
            return self.get(session_id)

    def reject_authorization(
        self,
        session_id: str,
        *,
        reason: str,
        errors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._connection()
            with conn:
                row = self._row(conn, session_id)
                self._require_active(row)
                conn.execute(
                    "UPDATE sessions SET state = 'degraded' WHERE session_id = ?",
                    (session_id,),
                )
                self._append_event(
                    conn,
                    session_id,
                    "authorize_rejected",
                    "studio",
                    {"reason": reason, "errors": errors or []},
                )
            return self.get(session_id)

    def degrade(
        self,
        session_id: str,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._connection()
            with conn:
                row = self._row(conn, session_id)
                self._require_active(row)
                conn.execute(
                    "UPDATE sessions SET state = 'degraded' WHERE session_id = ?",
                    (session_id,),
                )
                self._append_event(
                    conn,
                    session_id,
                    "degraded",
                    "studio",
                    {"reason": reason, "details": details or {}},
                )
            return self.get(session_id)

    def close_session(self, session_id: str, *, reason: str) -> dict[str, Any]:
        with self._lock:
            conn = self._connection()
            with conn:
                row = self._row(conn, session_id)
                if row["state"] == "closed":
                    raise SessionStoreError(
                        "SESSION_CLOSED",
                        f"session {session_id} is already closed",
                    )
                self._close_row(conn, row, reason)
            return self.get(session_id)

    def _close_row(self, conn: sqlite3.Connection, row: sqlite3.Row, reason: str) -> None:
        session_id = row["session_id"]
        closed_at = utc_now_iso()
        conn.execute(
            "UPDATE sessions SET state = 'closed', closed_at = ?, close_reason = ? WHERE session_id = ?",
            (closed_at, reason, session_id),
        )
        self._append_event(conn, session_id, "closed", "studio", {"reason": reason})
        record = self._record(conn, self._row(conn, session_id))
        conn.execute(
            "UPDATE sessions SET record_hash = ? WHERE session_id = ?",
            (_hash(record), session_id),
        )

    def close_stale_sessions(self, *, reason: str = "backend restart") -> int:
        """Close sessions left active by a previous backend process."""
        with self._lock:
            conn = self._connection()
            closed = 0
            with conn:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE state != 'closed' ORDER BY opened_at"
                ).fetchall()
                for row in rows:
                    self._close_row(conn, row, reason)
                    closed += 1
            return closed

    # ----------------------------------------------------------------- reads
    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            conn = self._connection()
            row = self._row(conn, session_id)
            return {**self._record(conn, row), "record_hash": row["record_hash"]}

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connection()
            row = self._active_row(conn)
            if row is None:
                return None
            return {**self._record(conn, row), "record_hash": row["record_hash"]}

    def list_sessions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connection()
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY opened_at DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            return [self._summary(row) for row in rows]

    def export(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            conn = self._connection()
            row = self._row(conn, session_id)
            record = self._record(conn, row)
            digest = _hash(record)
            stored = row["record_hash"]
            if stored and stored != digest:
                raise SessionStoreError(
                    "SESSION_HASH_MISMATCH",
                    "session record hash does not match the stored seal",
                    status_code=409,
                )
            self.export_dir.mkdir(parents=True, exist_ok=True)
            path = self.export_dir / f"{session_id}.json"
            payload = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "record_hash": digest,
                "record": record,
            }
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, path)
            return {"session_id": session_id, "export_path": str(path), "record_hash": digest}

    # ---------------------------------------------------------------- helpers
    def _row(self, conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (str(session_id),)
        ).fetchone()
        if row is None:
            raise SessionStoreError(
                "SESSION_NOT_FOUND",
                f"session {session_id} does not exist",
                status_code=404,
            )
        return row

    @staticmethod
    def _active_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM sessions WHERE state != 'closed' ORDER BY opened_at DESC LIMIT 1"
        ).fetchone()

    @staticmethod
    def _require_active(row: sqlite3.Row) -> None:
        if row["state"] == "closed":
            raise SessionStoreError(
                "SESSION_CLOSED",
                f"session {row['session_id']} is closed; open a new session",
            )

    def _append_event(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        kind: str,
        provenance: str,
        payload: dict[str, Any],
    ) -> None:
        if kind not in EVENT_TYPES:
            raise SessionStoreError("SESSION_INVALID", f"unknown event type {kind}", status_code=400)
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM session_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO session_events (event_id, session_id, seq, at, type, provenance, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"evt-{uuid.uuid4().hex[:12]}",
                session_id,
                int(seq),
                utc_now_iso(),
                kind,
                provenance,
                _canonical(payload),
            ),
        )

    def _events(self, conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM session_events WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "seq": int(row["seq"]),
                "at": row["at"],
                "type": row["type"],
                "provenance": row["provenance"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def _record(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        events = self._events(conn, row["session_id"])
        degraded_periods: list[dict[str, str]] = []
        open_at: str | None = None
        for event in events:
            if event["type"] == "degraded" and open_at is None:
                open_at = event["at"]
            elif event["type"] in {"authorized", "closed"} and open_at is not None:
                degraded_periods.append({"from": open_at, "to": event["at"]})
                open_at = None
        return {
            "schema_version": int(row["schema_version"]),
            "session_id": row["session_id"],
            "opened_at": row["opened_at"],
            "closed_at": row["closed_at"],
            "operator": row["operator"],
            "purpose": row["purpose"],
            "studio_version": row["studio_version"],
            "studio_commit": row["studio_commit"],
            "contract_hash": row["contract_hash"],
            "gateway_url": row["gateway_url"],
            "robot_profile": row["robot_profile"],
            "calibration": json.loads(row["calibration_json"]),
            "state": row["state"],
            "close_reason": row["close_reason"],
            "actions": [],
            "degraded_periods": degraded_periods,
            "events": events,
        }

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": int(row["schema_version"]),
            "session_id": row["session_id"],
            "opened_at": row["opened_at"],
            "closed_at": row["closed_at"],
            "operator": row["operator"],
            "purpose": row["purpose"],
            "state": row["state"],
            "contract_hash": row["contract_hash"],
            "close_reason": row["close_reason"],
            "record_hash": row["record_hash"],
        }
