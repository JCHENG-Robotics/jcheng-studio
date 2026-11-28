"""M2b intent tests against a loopback mock gateway.

The mock implements only ``/v1/motion/validate`` and
``/v1/motion/execute``. It records every path so a lease acquisition by the
Studio would fail the test.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.gateway_v1 import EXPECTED_CONTRACT_HASH
from app.adapters.gateway_v1_intent import GatewayV1IntentClient
from app.domain.enums import Mode
from app.main import create_app

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
MOTION_RESULT = {
    "intent_id": "intent-test",
    "outcome": "COMPLETED",
    "reason_code": "OK",
    "message": "mock completed",
    "safety": {"state": "ARMED_IDLE", "stop_latched": False},
    "measured_positions": [0.0] * 5,
    "bus_verified": True,
    "calibration_invalidated": False,
    "completed_at": "2026-11-26T00:00:00Z",
    "evidence_id": "ev-1",
}


class _IntentHandler(BaseHTTPRequestHandler):
    server_version = "MockIntentGateway/1.0"
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        self.server.calls.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "body": body,
                "headers": {key.lower(): value for key, value in self.headers.items()},
            }
        )
        if self.headers.get("Authorization") != "Bearer test-token":
            self._send(401, {"error": {"code": "UNAUTHORIZED", "message": "bad token"}})
            return
        if self.path == "/v1/motion/validate":
            approved = body.get("lease_id") == "lease-1"
            self._send(
                200,
                {
                    "approved": approved,
                    "reason_code": "OK" if approved else "NO_LEASE",
                    "violations": [] if approved else ["lease is not valid"],
                    "report": {},
                    "validation_id": "val-1",
                    "expires_at": "2026-11-26T00:00:00Z",
                },
            )
            return
        if self.path == "/v1/motion/execute":
            if not self.headers.get("Idempotency-Key"):
                self._send(
                    400,
                    {"error": {"code": "INVALID_COMMAND", "message": "missing key"}},
                )
                return
            result = dict(MOTION_RESULT)
            if getattr(self.server, "unknown_result", False):
                result["outcome"] = "UNKNOWN"
                result["reason_code"] = "MOTION_OUTCOME_UNKNOWN"
                result["bus_verified"] = False
            self._send(200, result)
            return
        self._send(404, {"error": {"code": "NOT_FOUND", "message": self.path}})

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture()
def intent_gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _IntentHandler)
    server.calls = []  # type: ignore[attr-defined]
    server.unknown_result = False  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _client(intent_gateway, settings, monkeypatch) -> TestClient:
    url, server = intent_gateway
    app = create_app(
        settings=replace(
            settings,
            # Intent is hardware-only and opt-in; mock mode is refused.
            mode=Mode.HARDWARE,
            system_intent_enabled=True,
            gateway_v1_url=url,
            gateway_v1_token="test-token",
        )
    )
    client = TestClient(app)
    client.__enter__()
    monkeypatch.setattr(
        client.app.state.service, "gateway_v1_status", lambda: READY
    )
    return client


def _authorized(client: TestClient) -> dict:
    opened = client.post(
        "/api/session/open", json={"operator": "operator", "purpose": "intent"}
    )
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session"]["session_id"]
    authorized = client.post(
        "/api/session/authorize", json={"session_id": session_id}
    )
    assert authorized.status_code == 200, authorized.text
    return authorized.json()["session"]


def test_intent_requires_an_authorized_session(intent_gateway, settings, monkeypatch) -> None:
    client = _client(intent_gateway, settings, monkeypatch)
    try:
        opened = client.post(
            "/api/session/open", json={"operator": "operator", "purpose": "no-auth"}
        )
        session_id = opened.json()["session"]["session_id"]
        response = client.post(
            "/api/session/intent/joint",
            json={
                "session_id": session_id,
                "lease_id": "lease-1",
                "joint": "joint1",
                "target_rad": 0.05,
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SESSION_NOT_AUTHORIZED"
    finally:
        client.__exit__(None, None, None)
    _, server = intent_gateway
    assert server.calls == []


def test_joint_intent_validates_and_executes(intent_gateway, settings, monkeypatch) -> None:
    client = _client(intent_gateway, settings, monkeypatch)
    try:
        session = _authorized(client)
        response = client.post(
            "/api/session/intent/joint",
            json={
                "session_id": session["session_id"],
                "lease_id": "lease-1",
                "joint": "joint1",
                "target_rad": 0.05,
                "duration_s": 1.0,
                "max_velocity_rad_s": 0.35,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"]["approved"] is True
        assert body["result"]["outcome"] == "COMPLETED"
        assert body["result"]["bus_verified"] is True
        record = client.get(f"/api/session/{session['session_id']}").json()["session"]
        assert record["actions"]
        assert any(event["type"] == "intent_executed" for event in record["events"])
    finally:
        client.__exit__(None, None, None)
    _, server = intent_gateway
    paths = [call["path"] for call in server.calls]
    assert paths == ["/v1/motion/validate", "/v1/motion/execute"]
    assert "/v1/leases" not in paths
    command = server.calls[0]["body"]
    assert command["kind"] == "JOINT_STEP"
    assert command["profile"] == "step"
    assert command["joint_names"] == ["joint1"]
    assert command["target_positions"] == [0.05]
    assert command["source"].startswith("studio:sess-")
    assert command["contract"]["contract_hash"] == EXPECTED_CONTRACT_HASH


def test_gripper_intent_validates_and_executes(intent_gateway, settings, monkeypatch) -> None:
    client = _client(intent_gateway, settings, monkeypatch)
    try:
        session = _authorized(client)
        response = client.post(
            "/api/session/intent/gripper",
            json={
                "session_id": session["session_id"],
                "lease_id": "lease-1",
                "position_rad": 0.8,
                "duration_s": 2.0,
                "max_velocity_rad_s": 0.5,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["outcome"] == "COMPLETED"
    finally:
        client.__exit__(None, None, None)
    _, server = intent_gateway
    command = server.calls[0]["body"]
    assert command["kind"] == "GRIPPER"
    assert command["profile"] == "gripper"
    assert command["gripper"]["position_rad"] == 0.8
    assert command["gripper"]["verify_bus"] is True


def test_intent_rejected_by_validation(intent_gateway, settings, monkeypatch) -> None:
    client = _client(intent_gateway, settings, monkeypatch)
    try:
        session = _authorized(client)
        response = client.post(
            "/api/session/intent/joint",
            json={
                "session_id": session["session_id"],
                "lease_id": "bad-lease",
                "joint": "joint1",
                "target_rad": 0.05,
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INTENT_REJECTED"
        record = client.get(f"/api/session/{session['session_id']}").json()["session"]
        assert any(event["type"] == "intent_rejected" for event in record["events"])
    finally:
        client.__exit__(None, None, None)
    _, server = intent_gateway
    assert [call["path"] for call in server.calls] == ["/v1/motion/validate"]


def test_intent_requires_a_lease_id(intent_gateway, settings, monkeypatch) -> None:
    client = _client(intent_gateway, settings, monkeypatch)
    try:
        session = _authorized(client)
        response = client.post(
            "/api/session/intent/joint",
            json={
                "session_id": session["session_id"],
                "lease_id": "",
                "joint": "joint1",
                "target_rad": 0.05,
            },
        )
        assert response.status_code == 422
    finally:
        client.__exit__(None, None, None)
    _, server = intent_gateway
    assert server.calls == []


def test_unknown_execution_degrades_the_session(intent_gateway, settings, monkeypatch) -> None:
    client = _client(intent_gateway, settings, monkeypatch)
    _, server = intent_gateway
    server.unknown_result = True  # type: ignore[attr-defined]
    try:
        session = _authorized(client)
        response = client.post(
            "/api/session/intent/joint",
            json={
                "session_id": session["session_id"],
                "lease_id": "lease-1",
                "joint": "joint1",
                "target_rad": 0.05,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["outcome"] == "UNKNOWN"
        record = client.get(f"/api/session/{session['session_id']}").json()["session"]
        assert record["state"] == "degraded"
        assert any(event["type"] == "intent_unknown" for event in record["events"])
    finally:
        client.__exit__(None, None, None)


def test_intent_requires_a_configured_gateway(settings, monkeypatch) -> None:
    # The intent gate itself passes (hardware + opt-in); the missing /v1
    # coordinates are what must fail closed.
    app = create_app(
        settings=replace(
            settings,
            mode=Mode.HARDWARE,
            system_intent_enabled=True,
            gateway_v1_url=None,
            gateway_v1_token=None,
        )
    )
    client = TestClient(app)
    client.__enter__()
    try:
        monkeypatch.setattr(
            client.app.state.service, "gateway_v1_status", lambda: READY
        )
        session = _authorized(client)
        response = client.post(
            "/api/session/intent/joint",
            json={
                "session_id": session["session_id"],
                "lease_id": "lease-1",
                "joint": "joint1",
                "target_rad": 0.05,
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "GATEWAY_V1_UNAVAILABLE"
    finally:
        client.__exit__(None, None, None)


def test_intent_client_has_no_lease_or_stop_methods(intent_gateway) -> None:
    url, _ = intent_gateway
    client = GatewayV1IntentClient(url, token="test-token")
    for name in ("acquire_lease", "release_lease", "renew_lease", "stop", "stop_motion"):
        assert not hasattr(client, name)
