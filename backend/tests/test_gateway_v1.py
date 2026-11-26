"""Read-only ``/v1`` gateway client tests against a mock HTTP server.

No DDS domain, device, ROS process or motion command is involved. The mock
server implements only the four GET routes the client is allowed to use, and
records every method so a mutation would fail the test.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.adapters.gateway_v1 import (
    EXPECTED_CONTRACT_HASH,
    GatewayV1Client,
    GatewayV1Error,
)
from app.main import create_app

CONTRACT = {
    "major": 0,
    "minor": 1,
    "patch": 0,
    "contract_hash": EXPECTED_CONTRACT_HASH,
}
PROFILE_HASH = "sha256:" + "1" * 64
CONFIG_HASH = "sha256:" + "2" * 64
SAFETY = {
    "header": {"stamp": "2026-11-26T00:00:00Z", "frame_id": "base_link"},
    "seq": 7,
    "state": "ARMED_IDLE",
    "stop_latched": False,
    "violation_codes": [],
    "violations": [],
    "config_hash": CONFIG_HASH,
    "profile_hash": PROFILE_HASH,
    "last_valid_state_at": "2026-11-26T00:00:00Z",
}
STATE = {
    "header": {"stamp": "2026-11-26T00:00:00Z", "frame_id": "base_link"},
    "seq": 11,
    "source": "mock",
    "fresh": True,
    "joint_names": ["joint1", "joint2", "joint3", "joint4", "joint5"],
    "positions": [0.0, 0.0, 0.0, 0.0, 0.0],
    "velocities": [0.0, 0.0, 0.0, 0.0, 0.0],
    "gripper_position": 0.0,
    "base_duty": [0.0, 0.0, 0.0, 0.0],
    "battery_voltage": 7.6,
    "profile_hash": PROFILE_HASH,
    "truth": "measured",
    "verification": "bus",
}


class _GatewayHandler(BaseHTTPRequestHandler):
    server_version = "MockGatewayV1/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        self.server.requests.append(("GET", self.path))  # type: ignore[attr-defined]
        if self.path == "/v1/health":
            self._send(
                200,
                {
                    "status": "ok",
                    "contract": CONTRACT,
                    "safety": SAFETY,
                    "capabilities": ["mock"],
                },
            )
            return
        if self.headers.get("Authorization") != "Bearer test-token":
            self._send(
                401,
                {
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "missing or invalid token",
                    }
                },
            )
            return
        if self.path == "/v1/contract":
            self._send(
                200,
                {
                    "contract": CONTRACT,
                    "profile_hash": PROFILE_HASH,
                    "system_version": "0.1.0",
                },
            )
        elif self.path == "/v1/state":
            self._send(200, STATE)
        elif self.path == "/v1/safety":
            if getattr(self.server, "wrap_safety", False):
                # The read-only gateway wraps the state; the motion gateway
                # returns the bare SafetyState. Both must work.
                self._send(200, {"safety": SAFETY, "authoritative": False})
            else:
                self._send(200, SAFETY)
        else:
            self._send(
                404,
                {"error": {"code": "NOT_FOUND", "message": "unknown path"}},
            )

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        self.server.requests.append(("POST", self.path))  # type: ignore[attr-defined]
        self._send(
            405,
            {
                "error": {
                    "code": "METHOD_NOT_ALLOWED",
                    "message": "read-only test gateway",
                }
            },
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture()
def gateway_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _client(url: str, **kwargs: Any) -> GatewayV1Client:
    return GatewayV1Client(url, token="test-token", timeout_s=1.0, **kwargs)


def test_expected_contract_hash_matches_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = yaml.safe_load((root / "capability.yaml").read_text(encoding="utf-8"))
    assert manifest["contract"]["contract_hash"] == EXPECTED_CONTRACT_HASH


def test_health_is_unauthenticated(gateway_url) -> None:
    url, server = gateway_url
    payload = GatewayV1Client(url).health()
    assert payload["status"] == "ok"
    assert payload["contract"]["contract_hash"] == EXPECTED_CONTRACT_HASH
    assert server.requests == [("GET", "/v1/health")]


def test_authenticated_reads_reject_a_wrong_token(gateway_url) -> None:
    url, _ = gateway_url
    for method in ("contract", "state", "safety"):
        with pytest.raises(GatewayV1Error) as caught:
            getattr(GatewayV1Client(url, token="wrong"), method)()
        assert caught.value.status == 401
        assert caught.value.code == "UNAUTHORIZED"

    assert (
        _client(url).contract()["contract"]["contract_hash"]
        == EXPECTED_CONTRACT_HASH
    )
    assert _client(url).state()["seq"] == 11
    assert _client(url).safety()["state"] == "ARMED_IDLE"


def test_probe_reports_ready_read_only(gateway_url) -> None:
    url, server = gateway_url
    status = _client(url).probe().to_public_dict()
    assert status["configured"] is True
    assert status["reachable"] is True
    assert status["authenticated"] is True
    assert status["read_only"] is True
    assert status["ready"] is True
    assert status["contract_match"] is True
    assert status["state_fresh"] is True
    assert status["state_truth"] == "measured"
    assert status["state_seq"] == 11
    assert status["joint_count"] == 5
    assert status["battery_voltage"] == 7.6
    assert status["safety_state"] == "ARMED_IDLE"
    assert status["stop_latched"] is False
    assert status["system_version"] == "0.1.0"
    assert status["profile_hash"] == PROFILE_HASH
    assert status["errors"] == []
    assert all(method == "GET" for method, _ in server.requests)


def test_readonly_safety_wrapper_is_unwrapped(gateway_url) -> None:
    url, server = gateway_url
    server.wrap_safety = True  # type: ignore[attr-defined]
    assert _client(url).safety()["state"] == "ARMED_IDLE"
    status = _client(url).probe().to_public_dict()
    assert status["safety_state"] == "ARMED_IDLE"
    assert status["ready"] is True


def test_probe_rejects_contract_mismatch(gateway_url) -> None:
    url, _ = gateway_url
    status = (
        _client(url, expected_contract_hash="sha256:" + "0" * 64)
        .probe()
        .to_public_dict()
    )
    assert status["contract_match"] is False
    assert status["ready"] is False
    assert any(item["code"] == "CONTRACT_MISMATCH" for item in status["errors"])


def test_probe_reports_unauthenticated_reads(gateway_url) -> None:
    url, _ = gateway_url
    status = GatewayV1Client(url).probe().to_public_dict()
    assert status["reachable"] is True
    assert status["authenticated"] is False
    assert status["ready"] is False
    # Health is unauthenticated and still carries the contract and safety.
    assert status["contract_match"] is True
    assert status["safety_state"] == "ARMED_IDLE"
    assert status["state_fresh"] is None
    assert any(item["code"] == "TOKEN_MISSING" for item in status["errors"])


def test_probe_reports_unreachable() -> None:
    status = (
        GatewayV1Client(
            "http://127.0.0.1:9", token="test-token", timeout_s=0.2
        )
        .probe()
        .to_public_dict()
    )
    assert status["reachable"] is False
    assert status["ready"] is False
    assert any(item["code"] == "UNREACHABLE" for item in status["errors"])


def test_client_has_no_mutation_methods(gateway_url) -> None:
    url, _ = gateway_url
    client = _client(url)
    for name in (
        "post",
        "execute",
        "stop",
        "acquire_lease",
        "release_lease",
        "arm",
        "disarm",
    ):
        assert not hasattr(client, name)


def test_status_api_is_read_only(gateway_url, settings) -> None:
    url, server = gateway_url
    configured = replace(
        settings,
        gateway_v1_url=url,
        gateway_v1_token="test-token",
    )
    app = create_app(settings=configured)
    with TestClient(app) as client:
        response = client.get("/api/gateway/v1/status")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["configured"] is True
        assert body["reachable"] is True
        assert body["ready"] is True
        assert body["read_only"] is True
    assert all(method == "GET" for method, _ in server.requests)
