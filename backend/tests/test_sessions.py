"""M2 operator-session tests: lifecycle, authorization gate, seal and export."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.adapters.gateway_v1 import EXPECTED_CONTRACT_HASH
from app.main import create_app
from app.sessions import SessionStore
from dataclasses import replace

from fastapi.testclient import TestClient

READY = {
    "configured": True,
    "reachable": True,
    "authenticated": True,
    "read_only": True,
    "ready": True,
    "health_status": "ok",
    "contract_hash": EXPECTED_CONTRACT_HASH,
    "contract_match": True,
    "state_fresh": True,
    "state_truth": "measured",
    "state_seq": 1,
    "joint_count": 5,
    "battery_voltage": 7.6,
    "safety_state": "DISARMED",
    "stop_latched": False,
    "violation_codes": [],
    "system_version": "0.1.0",
    "profile_hash": "sha256:" + "1" * 64,
    "latency_ms": 1,
    "errors": [],
}


def _open(client: TestClient, operator: str = "operator", purpose: str = "debug") -> dict:
    response = client.post(
        "/api/session/open", json={"operator": operator, "purpose": purpose}
    )
    assert response.status_code == 200, response.text
    return response.json()["session"]


def _set_ready(client: TestClient, monkeypatch, ready: dict | None = None) -> None:
    monkeypatch.setattr(
        client.app.state.service, "gateway_v1_status", lambda: ready or READY
    )


def test_session_lifecycle_and_export(client, monkeypatch) -> None:
    session = _open(client)
    session_id = session["session_id"]
    assert session["state"] == "open"
    assert session["operator"] == "operator"
    assert session["purpose"] == "debug"
    assert session["record_hash"] is None

    current = client.get("/api/session/current").json()["session"]
    assert current["session_id"] == session_id

    rejected = client.post(
        "/api/session/authorize", json={"session_id": session_id}
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "SESSION_AUTHORIZATION_REJECTED"
    degraded = client.get(f"/api/session/{session_id}").json()["session"]
    assert degraded["state"] == "degraded"
    assert any(event["type"] == "authorize_rejected" for event in degraded["events"])

    _set_ready(client, monkeypatch)
    authorized = client.post(
        "/api/session/authorize", json={"session_id": session_id}
    )
    assert authorized.status_code == 200, authorized.text
    session = authorized.json()["session"]
    assert session["state"] == "authorized"
    assert session["contract_hash"] == EXPECTED_CONTRACT_HASH
    assert session["robot_profile"] == READY["profile_hash"]
    assert any(event["type"] == "authorized" for event in session["events"])

    closed = client.post(
        "/api/session/close",
        json={"session_id": session_id, "reason": "operator finished"},
    )
    assert closed.status_code == 200
    session = closed.json()["session"]
    assert session["state"] == "closed"
    assert session["closed_at"]
    assert session["close_reason"] == "operator finished"
    assert session["record_hash"].startswith("sha256:")
    assert any(event["type"] == "closed" for event in session["events"])

    exported = client.get(f"/api/session/{session_id}/export")
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["record_hash"] == session["record_hash"]
    path = Path(body["export_path"])
    assert path.is_file()
    payload = json.loads(path.read_text())
    assert payload["record_hash"] == session["record_hash"]
    assert payload["record"]["session_id"] == session_id
    assert payload["record"]["state"] == "closed"

    # A closed session stays closed; a new session is allowed.
    again = client.post(
        "/api/session/close",
        json={"session_id": session_id, "reason": "again"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "SESSION_CLOSED"
    second = _open(client, purpose="second")
    assert second["session_id"] != session_id


def test_one_active_session_only(client) -> None:
    first = _open(client)
    response = client.post(
        "/api/session/open", json={"operator": "other", "purpose": "second"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_CONFLICT"
    assert client.get("/api/session/current").json()["session"]["session_id"] == first["session_id"]


def test_session_not_found(client) -> None:
    response = client.get("/api/session/sess-missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"
    response = client.post(
        "/api/session/authorize", json={"session_id": "sess-missing"}
    )
    assert response.status_code == 404


def test_authorization_requires_a_ready_gateway(client, monkeypatch) -> None:
    session = _open(client)
    _set_ready(
        client,
        monkeypatch,
        {
            **READY,
            "contract_match": False,
            "ready": False,
            "errors": [{"code": "CONTRACT_MISMATCH", "message": "mismatch", "status": None}],
        },
    )
    response = client.post(
        "/api/session/authorize", json={"session_id": session["session_id"]}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "SESSION_AUTHORIZATION_REJECTED"
    assert body["error"]["details"]["status"]["contract_match"] is False
    assert body["error"]["details"]["session"]["state"] == "degraded"


def test_export_detects_tampering(client, monkeypatch, settings) -> None:
    session = _open(client)
    _set_ready(client, monkeypatch)
    client.post("/api/session/authorize", json={"session_id": session["session_id"]})
    client.post(
        "/api/session/close",
        json={"session_id": session["session_id"], "reason": "seal"},
    )
    with sqlite3.connect(settings.session_db_path) as conn:
        conn.execute(
            "UPDATE session_events SET payload_json = '{}' WHERE session_id = ? AND seq = 1",
            (session["session_id"],),
        )
        conn.commit()
    response = client.get(f"/api/session/{session['session_id']}/export")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_HASH_MISMATCH"


def test_gateway_url_and_token_are_not_stored(settings) -> None:
    configured = replace(
        settings,
        gateway_v1_url="http://operator:secret@127.0.0.1:8766/gateway?token=secret",
        gateway_v1_token="secret-token",
    )
    app = create_app(settings=configured)
    with TestClient(app) as client:
        session = _open(client, operator="alice", purpose="redaction")
        client.post(
            "/api/session/close",
            json={"session_id": session["session_id"], "reason": "done"},
        )
        exported = client.get(f"/api/session/{session['session_id']}/export").json()
        text = Path(exported["export_path"]).read_text()
    assert "secret-token" not in text
    assert "operator:secret" not in text
    assert "?token=secret" not in text
    assert "http://127.0.0.1:8766/gateway" in text


def test_backend_restart_closes_an_active_session(tmp_path: Path) -> None:
    store = SessionStore(
        tmp_path / "sessions.db",
        tmp_path / "exports",
        studio_version="0.5.0",
        studio_commit="test",
        robot_id="armpi-ultra-01",
        gateway_url="http://127.0.0.1:8766",
        calibration={"camera": "handeye-tsai-20261117"},
    )
    session = store.open_session(operator="operator", purpose="restart")
    assert session["state"] == "open"
    restarted = SessionStore(
        tmp_path / "sessions.db",
        tmp_path / "exports",
        studio_version="0.5.0",
        studio_commit="test",
        robot_id="armpi-ultra-01",
        gateway_url="http://127.0.0.1:8766",
        calibration={"camera": "handeye-tsai-20261117"},
    )
    assert restarted.close_stale_sessions() == 1
    closed = restarted.get(session["session_id"])
    assert closed["state"] == "closed"
    assert closed["close_reason"] == "backend restart"
    assert closed["record_hash"].startswith("sha256:")
    store.close()
    restarted.close()
