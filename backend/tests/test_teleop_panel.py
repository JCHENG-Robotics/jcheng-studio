"""M5 teleop panel backend: status proxy and system stop tests."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

import pytest

from app.adapters.mock import MockRobotAdapter
from app.config import Settings
from app.errors import ServiceError
from app.persistence.store import EventStore
from app.service import RobotService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = PROJECT_ROOT / "configs" / "joint_mapping.yaml"

STATUS_PAYLOAD = {
    "schema": "jcheng.teleop.status.v1",
    "teleop_version": "0.3.0",
    "contract_hash": "sha256:" + "1" * 64,
    "profile_id": "armpi_ultra_demo",
    "profile_hash": "sha256:" + "2" * 64,
    "lease_ref": "sha256:" + "3" * 64,
    "phase": "running",
    "generated_at_ns": 1,
    "stale_after_ms": 1000,
    "input": {
        "present": True,
        "age_ms": 20.0,
        "stale": False,
        "deadman": True,
        "mode": "JOINT_VELOCITY",
        "source_id": "ros_virtual",
        "seq": 3,
        "t_source_ns": 60_000_000,
    },
    "last_wire_seq": 2,
    "capture": {"enabled": False},
    "limitations": ["read-only"],
}


@contextmanager
def _server(routes: dict[str, tuple[int, dict, bytes]]):
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            seen.append(
                {
                    "method": method,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": body,
                }
            )
            status, headers, payload = routes.get(
                self.path, (404, {"Content-Type": "application/json"}, b'{"error":{}}')
            )
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._respond("GET")

        def do_POST(self) -> None:
            self._respond("POST")

        def log_message(self, format: str, *args) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)


def _service(settings: Settings, mapping, clock) -> RobotService:
    return RobotService(
        settings,
        mapping,
        MockRobotAdapter(mapping, clock=clock),
        EventStore(settings.db_path),
        clock=clock,
    )


def test_status_not_configured_is_degraded(service: RobotService) -> None:
    payload = service.teleop_status()
    assert payload["configured"] is False
    assert payload["degraded"] is True
    assert payload["errors"][0]["code"] == "NOT_CONFIGURED"


def test_status_proxy_reports_fresh_sample(settings: Settings, mapping, clock) -> None:
    body = json.dumps(STATUS_PAYLOAD).encode()
    with _server({"/v1/status": (200, {"Content-Type": "application/json"}, body)}) as (url, seen):
        svc = _service(replace(settings, teleop_status_url=url), mapping, clock)
        payload = svc.teleop_status()
    assert payload["configured"] is True
    assert payload["valid"] is True
    assert payload["stale"] is False
    assert payload["degraded"] is False
    assert payload["phase"] == "running"
    assert payload["deadman"] is True
    assert payload["lease_ref"].startswith("sha256:")
    assert seen[0]["path"] == "/v1/status"


def test_status_proxy_marks_an_old_sample_stale(settings: Settings, mapping, clock) -> None:
    old = json.loads(json.dumps(STATUS_PAYLOAD))
    old["input"]["age_ms"] = 2000.0
    body = json.dumps(old).encode()
    with _server({"/v1/status": (200, {"Content-Type": "application/json"}, body)}) as (url, _seen):
        svc = _service(replace(settings, teleop_status_url=url), mapping, clock)
        payload = svc.teleop_status()
    assert payload["valid"] is True
    assert payload["stale"] is True
    assert payload["degraded"] is True


def test_status_proxy_rejects_a_bad_schema(settings: Settings, mapping, clock) -> None:
    body = json.dumps({"schema": "other"}).encode()
    with _server({"/v1/status": (200, {"Content-Type": "application/json"}, body)}) as (url, _seen):
        svc = _service(replace(settings, teleop_status_url=url), mapping, clock)
        payload = svc.teleop_status()
    assert payload["valid"] is False
    assert payload["degraded"] is True
    assert payload["errors"][0]["code"] == "INVALID_PAYLOAD"


def test_status_proxy_handles_an_unreachable_server(settings: Settings, mapping, clock) -> None:
    svc = _service(
        replace(settings, teleop_status_url="http://127.0.0.1:9", teleop_status_timeout_s=0.2),
        mapping,
        clock,
    )
    payload = svc.teleop_status()
    assert payload["reachable"] is False
    assert payload["degraded"] is True


def test_status_proxy_rejects_a_non_loopback_url(settings: Settings, mapping, clock) -> None:
    svc = _service(
        replace(settings, teleop_status_url="http://192.168.1.5:8765"), mapping, clock
    )
    payload = svc.teleop_status()
    assert payload["valid"] is False
    assert payload["errors"][0]["code"] == "NON_LOOPBACK"


def test_system_stop_is_disabled_by_default(service: RobotService) -> None:
    with pytest.raises(ServiceError) as exc:
        service.system_stop(operator_id="op-1")
    assert exc.value.code == "SYSTEM_STOP_DISABLED"
    assert exc.value.status_code == 409


def test_system_stop_calls_the_system_endpoint(settings: Settings, mapping, clock) -> None:
    body = json.dumps({"accepted": True, "message": "software stop requested"}).encode()
    with _server({"/v1/motion/stop": (200, {"Content-Type": "application/json"}, body)}) as (
        url,
        seen,
    ):
        svc = _service(
            replace(
                settings,
                system_stop_enabled=True,
                gateway_v1_url=url,
                gateway_v1_token="test-token",
            ),
            mapping,
            clock,
        )
        result = svc.system_stop(operator_id="operator-1", reason="panel test")
    assert result["accepted"] is True
    assert result["latency_ms"] < 500
    request = seen[0]
    assert request["method"] == "POST"
    assert request["path"] == "/v1/motion/stop"
    assert request["headers"]["Authorization"] == "Bearer test-token"
    assert json.loads(request["body"]) == {
        "operator_id": "operator-1",
        "reason": "panel test",
    }


def test_system_stop_rejects_a_non_loopback_gateway(settings: Settings, mapping, clock) -> None:
    svc = _service(
        replace(
            settings,
            system_stop_enabled=True,
            gateway_v1_url="http://192.168.1.5:8766",
            gateway_v1_token="test-token",
        ),
        mapping,
        clock,
    )
    with pytest.raises(ServiceError) as exc:
        svc.system_stop(operator_id="op-1")
    assert exc.value.code == "SYSTEM_STOP_CONFIG"


def test_routes_expose_status_and_refuse_stop_when_disabled(client) -> None:
    status = client.get("/api/teleop/status")
    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert status.json()["degraded"] is True
    stop = client.post("/api/teleop/stop", json={"operator_id": "op-1"})
    assert stop.status_code == 409
    assert stop.json()["error"]["code"] == "SYSTEM_STOP_DISABLED"
