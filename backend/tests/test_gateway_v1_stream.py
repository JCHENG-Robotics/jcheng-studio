"""Read-only ``/v1/ws/state`` client tests against a loopback mock server.

The mock server uses the ``websockets`` sync API (a backend dependency). No
DDS domain, device, ROS process or motion command is involved.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from itertools import islice
from threading import Thread
from typing import Any
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from websockets.sync.server import serve

from app.adapters.gateway_v1 import EXPECTED_CONTRACT_HASH
from app.adapters.gateway_v1_stream import GatewayV1Stream
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


def _envelope(kind: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": kind,
            "id": f"mock-{kind}-{time.monotonic_ns()}",
            "ts": "2026-11-26T00:00:00Z",
            "payload": payload,
        }
    )


HELLO = _envelope(
    "hello",
    {
        "contract": CONTRACT,
        "profile_hash": PROFILE_HASH,
        "system_version": "0.1.0",
        "mode": "readonly",
        "read_only": True,
    },
)
STATE_ENVELOPE = _envelope("state", STATE)
SAFETY_ENVELOPE = _envelope("safety", SAFETY)


def _start(handler, *, process_request=None) -> tuple[Any, Thread, int]:
    server = serve(
        handler,
        "127.0.0.1",
        0,
        process_request=process_request,
        compression=None,
        ping_interval=None,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.socket.getsockname()[1])
    return server, thread, port


@pytest.fixture()
def stream_gateway():
    connections: list[dict[str, str]] = []

    def handler(websocket) -> None:
        connections.append(dict(websocket.request.headers))
        websocket.send(HELLO)
        websocket.send(STATE_ENVELOPE)
        websocket.send(SAFETY_ENVELOPE)
        time.sleep(0.1)

    server, thread, port = _start(handler)
    try:
        yield f"http://127.0.0.1:{port}", connections
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _client(url: str, **kwargs: Any) -> GatewayV1Stream:
    return GatewayV1Stream(url, token="test-token", timeout_s=2.0, **kwargs)


def test_stream_probe_reads_hello_state_and_safety(stream_gateway) -> None:
    url, connections = stream_gateway
    status = _client(url).probe(count=3).to_public_dict()
    assert status["configured"] is True
    assert status["reachable"] is True
    assert status["authenticated"] is True
    assert status["read_only"] is True
    assert status["ready"] is True
    assert status["message_types"] == ["hello", "state", "safety"]
    assert status["contract_match"] is True
    assert status["state_truth"] == "measured"
    assert status["state_fresh"] is True
    assert status["state_seq"] == 11
    assert status["joint_count"] == 5
    assert status["safety_state"] == "ARMED_IDLE"
    assert status["stop_latched"] is False
    assert status["errors"] == []
    assert connections
    assert connections[0].get("authorization") == "Bearer test-token"


def test_stream_probe_rejects_wrong_token() -> None:

    def process_request(connection, request):
        if request.headers.get("Authorization") != "Bearer test-token":
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        return None

    # The fixture server doesn't reject; start a dedicated one for this case.
    server, thread, port = _start(
        lambda websocket: websocket.send(HELLO), process_request=process_request
    )
    try:
        status = GatewayV1Stream(
            f"http://127.0.0.1:{port}", token="wrong", timeout_s=2.0
        ).probe(count=3).to_public_dict()
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert status["reachable"] is True
    assert status["authenticated"] is False
    assert status["ready"] is False
    assert any(item["code"] == "UNAUTHORIZED" for item in status["errors"])


def test_stream_probe_rejects_contract_mismatch(stream_gateway) -> None:
    url, _ = stream_gateway
    status = (
        _client(url, expected_contract_hash="sha256:" + "0" * 64)
        .probe(count=3)
        .to_public_dict()
    )
    assert status["contract_match"] is False
    assert status["ready"] is False
    assert any(item["code"] == "CONTRACT_MISMATCH" for item in status["errors"])


def test_stream_requires_hello_first() -> None:
    def handler(websocket) -> None:
        websocket.send(STATE_ENVELOPE)
        websocket.send(SAFETY_ENVELOPE)

    server, thread, port = _start(handler)
    try:
        status = GatewayV1Stream(
            f"http://127.0.0.1:{port}", token="test-token", timeout_s=2.0
        ).probe(count=3).to_public_dict()
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert status["authenticated"] is False
    assert status["ready"] is False
    assert any(item["code"] == "STREAM_CONTRACT" for item in status["errors"])


def test_stream_reconnects_with_bounded_attempts() -> None:
    connections: list[int] = []

    def handler(websocket) -> None:
        connections.append(1)
        websocket.send(HELLO)
        # Return immediately: the client sees a normal close and reconnects.

    server, thread, port = _start(handler)
    try:
        stream = GatewayV1Stream(
            f"http://127.0.0.1:{port}",
            token="test-token",
            timeout_s=2.0,
            backoff_s=0.01,
            max_attempts=2,
        )
        envelopes = list(islice(stream.stream(), 2))
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert [item["type"] for item in envelopes] == ["hello", "hello"]
    assert len(connections) >= 2


def test_stream_probe_reports_unreachable() -> None:
    status = (
        GatewayV1Stream(
            "http://127.0.0.1:9", token="test-token", timeout_s=0.2
        )
        .probe()
        .to_public_dict()
    )
    assert status["reachable"] is False
    assert status["ready"] is False
    assert any(item["code"] == "STREAM_CLOSED" for item in status["errors"])


def test_stream_has_no_mutation_methods(stream_gateway) -> None:
    url, _ = stream_gateway
    stream = _client(url)
    for name in ("post", "execute", "stop", "acquire_lease", "release_lease"):
        assert not hasattr(stream, name)


def test_stream_api_is_read_only(stream_gateway, settings) -> None:
    url, _ = stream_gateway
    configured = replace(
        settings,
        gateway_v1_url=url,
        gateway_v1_token="test-token",
    )
    app = create_app(settings=configured)
    with TestClient(app) as client:
        response = client.get("/api/gateway/v1/stream")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ready"] is True
        assert body["read_only"] is True
        assert body["message_types"] == ["hello", "state", "safety"]
