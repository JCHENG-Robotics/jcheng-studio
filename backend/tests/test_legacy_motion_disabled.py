"""Authority hardening: legacy motion is disabled by default and isolated.

The Studio is not a motion authority. The default runtime must not dispatch,
pulse or locally stop motion; the only supported paths are a System-issued
intent (hardware-only, opt-in) and the single system stop proxy. The deprecated
legacy entry points answer HTTP 410 Gone unless the operator set both
``ARMPI_MODE=legacy`` and ``ARMPI_ENABLE_LEGACY_MOTION=1``.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockRobotAdapter
from app.config import Settings
from app.domain.enums import Mode
from app.domain.models import MotionTarget
from app.errors import ServiceError
from app.main import create_app
from app.persistence.store import EventStore
from app.service import RobotService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = Path(__file__).resolve().parents[1] / "app"

EXECUTE_BODY = {
    "session_id": "s1",
    "lease_id": "system-lease-1",
    "preview_token": "token",
    "command_id": "cmd_1",
    "targets": [{"joint": "joint1", "target_rad": 0.05, "duration_s": 1.0}],
}
CHASSIS_BODY = {
    "session_id": "s1",
    "lease_id": "system-lease-1",
    "vx": 1.0,
    "vy": 0.0,
    "duty": 5.0,
    "duration_s": 0.5,
}
STOP_BODY = {"session_id": "s1"}

_ENV_VARS = (
    "ARMPI_MODE",
    "ARMPI_ENABLE_LEGACY_MOTION",
    "ARMPI_HARDWARE_MOTION",
    "ARMPI_SYSTEM_INTENT",
    "ARMPI_SYSTEM_STOP",
    "ARMPI_GATEWAY_URL",
    "ARMPI_GATEWAY_TOKEN",
    "ARMPI_GATEWAY_V1_URL",
    "ARMPI_GATEWAY_V1_TOKEN",
    "ARMPI_GATEWAY_V1_CONTRACT_HASH",
    "ARMPI_DEMO",
    "ARMPI_MOTION_SCOPE",
    "ARMPI_TAKEOVER_ACK",
    "ARMPI_SUPERVISED_MOTION_ACK",
    "ARMPI_SESSION_DB",
    "ARMPI_SESSION_EXPORTS",
    "ARMPI_ROBOT_ID",
    "ARMPI_JOINT_MAPPING",
)


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------- 410 Gone
def test_legacy_endpoints_answer_410_by_default(client: TestClient) -> None:
    responses = {
        "execute": client.post(
            "/api/motion/execute",
            headers={"Idempotency-Key": "key-1"},
            json=EXECUTE_BODY,
        ),
        "chassis": client.post("/api/chassis/pulse", json=CHASSIS_BODY),
        "stop": client.post("/api/motion/stop", json=STOP_BODY),
    }
    for operation, response in responses.items():
        assert response.status_code == 410, (operation, response.text)
        body = response.json()["error"]
        assert body["code"] == "LEGACY_MOTION_DISABLED"
        assert body["details"]["enable"].startswith("ARMPI_MODE=legacy")
        migration = body["details"]["migration"]
        assert "/api/session/intent/joint" in migration
        assert "/api/teleop/stop" in migration

    # The route dependency runs before body validation: a stale client with an
    # old/partial payload still gets the deprecation answer, not 422.
    for path in (
        "/api/motion/execute",
        "/api/chassis/pulse",
        "/api/motion/stop",
    ):
        response = client.post(path, json={})
        assert response.status_code == 410, (path, response.text)
        assert response.json()["error"]["code"] == "LEGACY_MOTION_DISABLED"


def test_legacy_empty_lease_is_rejected_and_never_dispatched(
    settings: Settings, clock, monkeypatch
) -> None:
    """Legacy mode has no system lease validation, so an empty lease must fail.

    The frozen gateway cannot check a System lease: a non-empty operator string
    is accepted as-is (operator-only, not system-authorized). An empty or
    whitespace-only lease is therefore rejected locally with 400 before any
    adapter call.
    """
    app = create_app(
        settings=replace(settings, legacy_motion_enabled=True), clock=clock
    )
    with TestClient(app) as client:
        adapter = client.app.state.service.adapter

        def _must_not_dispatch(*_args: Any, **_kwargs: Any):
            raise AssertionError("legacy motion was dispatched with an empty lease")

        monkeypatch.setattr(adapter, "dispatch", _must_not_dispatch)
        monkeypatch.setattr(adapter, "dispatch_profile", _must_not_dispatch)
        monkeypatch.setattr(adapter, "chassis_pulse", _must_not_dispatch)

        preview = client.post(
            "/api/motion/preview",
            json={
                "session_id": "s1",
                "targets": [
                    {"joint": "joint1", "target_rad": 0.05, "duration_s": 1.0}
                ],
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["valid"] is True, preview.text

        for index, empty_lease in enumerate(("", "   ")):
            response = client.post(
                "/api/motion/execute",
                headers={"Idempotency-Key": f"empty-lease-{index}"},
                json={
                    "session_id": "s1",
                    "lease_id": empty_lease,
                    "preview_token": preview.json()["preview_token"],
                    "command_id": preview.json()["command_id"],
                    "targets": [
                        {"joint": "joint1", "target_rad": 0.05, "duration_s": 1.0}
                    ],
                },
            )
            assert response.status_code in (400, 403), response.text
            assert response.json()["error"]["code"] in (
                "BAD_REQUEST",
                "NO_LEASE",
                "MISSING_SYSTEM_LEASE",
            )

        chassis = client.post(
            "/api/chassis/pulse",
            json={**CHASSIS_BODY, "lease_id": ""},
        )
        assert chassis.status_code in (400, 403), chassis.text
        assert chassis.json()["error"]["code"] in (
            "BAD_REQUEST",
            "NO_LEASE",
            "MISSING_SYSTEM_LEASE",
        )

        stages = [
            event["stage"] for event in client.get("/api/events").json()["items"]
        ]
        assert "Dispatched" not in stages


def test_non_legacy_service_calls_never_touch_the_adapter(
    settings: Settings, mapping, clock
) -> None:
    calls: list[str] = []

    class SpyAdapter(MockRobotAdapter):
        def dispatch(self, *args: Any, **kwargs: Any):
            calls.append("dispatch")
            return super().dispatch(*args, **kwargs)

        def dispatch_profile(self, *args: Any, **kwargs: Any):
            calls.append("dispatch_profile")
            return super().dispatch_profile(*args, **kwargs)

        def chassis_pulse(self, *args: Any, **kwargs: Any):
            calls.append("chassis_pulse")
            return super().chassis_pulse(*args, **kwargs)

        def stop(self, *args: Any, **kwargs: Any):
            calls.append("stop")
            return super().stop(*args, **kwargs)

    adapter = SpyAdapter(mapping, clock=clock)
    service = RobotService(
        replace(settings, legacy_motion_enabled=False),
        mapping,
        adapter,
        EventStore(settings.db_path),
        clock=clock,
    )
    try:
        targets = [MotionTarget("joint1", 0.05, 1.0)]
        calls_to_block = (
            lambda: service.execute(
                session_id="s1",
                lease_id="system-lease-1",
                preview_token="token",
                command_id="cmd_1",
                targets=targets,
                idempotency_key="key-1",
            ),
            lambda: service.stop(session_id="s1"),
            lambda: service.chassis_pulse(
                session_id="s1",
                lease_id="system-lease-1",
                vx=1.0,
                vy=0.0,
                duty=5.0,
                duration_s=0.5,
            ),
        )
        for call in calls_to_block:
            with pytest.raises(ServiceError) as caught:
                call()
            assert caught.value.status_code == 410
            assert caught.value.code == "LEGACY_MOTION_DISABLED"
    finally:
        service.close()
    assert calls == []


# ------------------------------------------------------------------ intent gate
def test_intent_is_refused_in_mock_mode(client: TestClient) -> None:
    joint = client.post(
        "/api/session/intent/joint",
        json={"session_id": "s1", "lease_id": "lease-1", "joint": "joint1", "target_rad": 0.05},
    )
    gripper = client.post(
        "/api/session/intent/gripper",
        json={"session_id": "s1", "lease_id": "lease-1", "position_rad": 0.2},
    )
    for response in (joint, gripper):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "SYSTEM_INTENT_REQUIRES_HARDWARE"


def test_intent_is_refused_in_hardware_without_the_switch(
    settings: Settings, clock
) -> None:
    hardware = replace(
        settings,
        mode=Mode.HARDWARE,
        system_intent_enabled=False,
        db_path=settings.db_path.with_name("intent-off.db"),
    )
    with TestClient(create_app(settings=hardware, clock=clock)) as client:
        response = client.post(
            "/api/session/intent/joint",
            json={
                "session_id": "s1",
                "lease_id": "lease-1",
                "joint": "joint1",
                "target_rad": 0.05,
            },
        )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "SYSTEM_INTENT_DISABLED"


# --------------------------------------------------------- single system stop
class _StopGateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).seen.append(
            {
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
        )
        payload = json.dumps({"accepted": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:
        return


def test_stop_goes_through_the_system_and_never_the_local_adapter(
    settings: Settings, clock, monkeypatch
) -> None:
    _StopGateway.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StopGateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        app = create_app(
            settings=replace(
                settings,
                system_stop_enabled=True,
                gateway_v1_url=url,
                gateway_v1_token="test-token",
            ),
            clock=clock,
        )
        with TestClient(app) as client:

            def _local_stop_must_not_run(*_args: Any, **_kwargs: Any):
                raise AssertionError("the local adapter stop was called")

            monkeypatch.setattr(
                client.app.state.service.adapter, "stop", _local_stop_must_not_run
            )
            response = client.post(
                "/api/teleop/stop", json={"operator_id": "operator-1"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["accepted"] is True

            legacy_stop = client.post("/api/motion/stop", json=STOP_BODY)
            assert legacy_stop.status_code == 410
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert len(_StopGateway.seen) == 1
    request = _StopGateway.seen[0]
    assert request["path"] == "/v1/motion/stop"
    assert request["headers"]["authorization"] == "Bearer test-token"
    assert json.loads(request["body"])["operator_id"] == "operator-1"


# ------------------------------------------------------------ static defaults
def test_default_modules_do_not_reference_the_legacy_dispatch_path() -> None:
    offenders = sorted(
        path.relative_to(APP_ROOT).as_posix()
        for path in APP_ROOT.rglob("*.py")
        if "/motion/dispatch" in path.read_text(encoding="utf-8")
    )
    assert offenders == ["legacy_motion.py"]


def test_motion_ops_do_not_use_the_local_lease_as_a_motion_gate() -> None:
    source = (APP_ROOT / "application" / "motion_ops.py").read_text(encoding="utf-8")
    assert "self.leases.check" not in source


def test_safety_report_treats_the_local_lock_as_advisory(service: RobotService) -> None:
    report = service.safety_report(require_lease=True)
    check = next(item for item in report["checks"] if item["check_id"] == "control_lease")
    assert check["blocking"] is False
    assert check["passed"] is False
    assert "system" in check["detail"]
    assert report["ready"] is True


# ------------------------------------------------------------ double switch
def test_legacy_mode_without_the_switch_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("ARMPI_MODE", "legacy")
    with pytest.raises(ValueError, match="ARMPI_ENABLE_LEGACY_MOTION=1"):
        Settings.from_env(db_path=str(tmp_path / "no-switch.db"))


def test_the_switch_alone_does_not_enable_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("ARMPI_MODE", "hardware")
    monkeypatch.setenv("ARMPI_ENABLE_LEGACY_MOTION", "1")
    settings = Settings.from_env(db_path=str(tmp_path / "switch-only.db"))
    assert settings.legacy_motion_enabled is False
    assert any("ARMPI_ENABLE_LEGACY_MOTION" in item for item in settings.startup_warnings)


def test_both_switches_enable_the_isolated_legacy_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mapping, clock
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("ARMPI_MODE", "legacy")
    monkeypatch.setenv("ARMPI_ENABLE_LEGACY_MOTION", "1")
    monkeypatch.setenv("ARMPI_GATEWAY_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ARMPI_GATEWAY_TOKEN", "token")
    monkeypatch.setenv("ARMPI_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("ARMPI_SESSION_EXPORTS", str(tmp_path / "exports"))

    legacy = Settings.from_env(db_path=str(tmp_path / "legacy.db"))
    assert legacy.mode is Mode.LEGACY
    assert legacy.legacy_motion_enabled is True
    assert legacy.hardware_motion_enabled is True

    # The legacy settings select the hardware adapter in create_app; replace it
    # with the mock adapter so this test touches no gateway or robot.
    import app.main as main_module

    monkeypatch.setattr(
        main_module,
        "HardwareAdapter",
        lambda *_args, **_kwargs: MockRobotAdapter(mapping, clock=clock),
    )
    app = create_app(settings=legacy, clock=clock)
    with TestClient(app) as client:
        lease = client.post(
            "/api/control/acquire", json={"session_id": "legacy-session"}
        )
        assert lease.status_code == 200, lease.text
        assert lease.json()["authority"] == "ui_concurrency_lock"

        preview = client.post(
            "/api/motion/preview",
            json={
                "session_id": "legacy-session",
                "targets": [
                    {"joint": "joint1", "target_rad": 0.05, "duration_s": 1.0}
                ],
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["valid"] is True, preview.text

        executed = client.post(
            "/api/motion/execute",
            headers={"Idempotency-Key": "legacy-view"},
            json={
                "session_id": "legacy-session",
                "lease_id": lease.json()["lease_id"],
                "preview_token": preview.json()["preview_token"],
                "command_id": preview.json()["command_id"],
                "targets": [
                    {"joint": "joint1", "target_rad": 0.05, "duration_s": 1.0}
                ],
            },
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "Dispatched"

        stopped = client.post("/api/motion/stop", json=STOP_BODY)
        assert stopped.status_code == 200, stopped.text


# ------------------------------------------------------- mock cannot reach v1
def test_mock_mode_clears_a_real_v1_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("ARMPI_MODE", "mock")
    monkeypatch.setenv("ARMPI_GATEWAY_V1_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ARMPI_GATEWAY_V1_TOKEN", "operator-token")
    monkeypatch.setenv("ARMPI_SYSTEM_STOP", "1")

    settings = Settings.from_env(db_path=str(tmp_path / "mock-guard.db"))
    assert settings.gateway_v1_url is None
    assert settings.gateway_v1_token is None
    assert settings.system_stop_enabled is False
    assert any("cleared" in item for item in settings.startup_warnings)
