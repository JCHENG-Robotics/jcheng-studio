from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import Mode
from app.main import create_app


def _acquire(client: TestClient, session_id: str = "s1") -> dict:
    response = client.post("/api/control/acquire", json={"session_id": session_id})
    assert response.status_code == 200, response.text
    return response.json()


def _preview(
    client: TestClient,
    *,
    session_id: str = "s1",
    joint: str = "joint1",
    target_rad: float = 0.1,
    duration_s: float = 1.0,
) -> dict:
    response = client.post(
        "/api/motion/preview",
        json={
            "session_id": session_id,
            "targets": [{"joint": joint, "target_rad": target_rad, "duration_s": duration_s}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _execute(
    client: TestClient,
    *,
    lease_id: str,
    preview: dict,
    session_id: str = "s1",
    key: str = "key-1",
    target_rad: float = 0.1,
) -> object:
    return client.post(
        "/api/motion/execute",
        headers={"Idempotency-Key": key},
        json={
            "session_id": session_id,
            "lease_id": lease_id,
            "preview_token": preview["preview_token"],
            "command_id": preview["command_id"],
            "targets": [{"joint": "joint1", "target_rad": target_rad, "duration_s": 1.0}],
        },
    )


def test_health_and_state_are_simulated(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"
    assert body["source_label"] == "SIMULATION"
    assert body["capabilities"]["hardware_motion"] is False

    state = client.get("/api/state")
    assert state.status_code == 200
    state_body = state.json()
    assert state_body["source_label"] == "SIMULATION"
    assert len(state_body["joints"]) == 5
    assert all(joint["truth"] == "simulated" for joint in state_body["joints"])


def test_diagnostics_exposes_verified_mapping(client: TestClient) -> None:
    response = client.get("/api/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["mapping"]["verified"] is True
    assert (
        body["mapping"]["verification_status"]
        == "measured_2026_11_19_operating_envelope"
    )
    assert len(body["known_defects"]) >= 6
    assert body["probe_available"] is True
    assert body["safety"]["ready"] is True


def test_preview_valid_and_invalid(client: TestClient) -> None:
    valid = _preview(client)
    assert valid["valid"] is True
    assert valid["preview_token"]
    assert valid["errors"] == []

    invalid = _preview(client, joint="joint3", target_rad=0.1)
    assert invalid["valid"] is False
    assert invalid["preview_token"] is None
    assert invalid["errors"][0]["code"] == "OUT_OF_LIMIT"


def test_execute_requires_lease(client: TestClient) -> None:
    preview = _preview(client)
    response = _execute(client, lease_id="missing", preview=preview)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NO_LEASE"


def test_preview_execute_and_complete(client: TestClient, clock) -> None:
    lease = _acquire(client)
    preview = _preview(client)
    response = _execute(client, lease_id=lease["lease_id"], preview=preview)
    assert response.status_code == 200, response.text
    command = response.json()
    assert command["status"] == "Dispatched"
    assert command["command_id"] == preview["command_id"]

    events = client.get("/api/events").json()["items"]
    stages = [event["stage"] for event in events]
    assert "Requested" in stages
    assert "Validated" in stages
    assert "Dispatched" in stages

    clock.advance(2.0)
    state = client.get("/api/state").json()
    assert state["joints"][0]["position_rad"] == 0.1
    events_after = client.get("/api/events").json()["items"]
    stages_after = [event["stage"] for event in events_after]
    assert "Observed" in stages_after
    assert "Completed" in stages_after


def test_idempotency_replays_same_command(client: TestClient) -> None:
    lease = _acquire(client)
    preview = _preview(client)
    first = _execute(client, lease_id=lease["lease_id"], preview=preview, key="same-key")
    assert first.status_code == 200
    second = _execute(client, lease_id=lease["lease_id"], preview=preview, key="same-key")
    assert second.status_code == 200
    assert second.json()["command_id"] == first.json()["command_id"]


def test_preview_token_single_use(client: TestClient) -> None:
    lease = _acquire(client)
    preview = _preview(client)
    first = _execute(client, lease_id=lease["lease_id"], preview=preview, key="first")
    assert first.status_code == 200
    second = _execute(client, lease_id=lease["lease_id"], preview=preview, key="second")
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "PREVIEW_REUSED"


def test_stop_is_not_emergency_stop(client: TestClient) -> None:
    response = client.post("/api/motion/stop", json={"session_id": "s1"})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "StopObserved"
    assert "emergency" in body["evidence"] or "not" in body["evidence"]


def test_mock_fault_injection_and_reset(client: TestClient) -> None:
    injected = client.post("/api/mock/faults", json={"kind": "empty_feedback"})
    assert injected.status_code == 200
    state = client.get("/api/state").json()
    assert all(joint["validity"] == "unavailable" for joint in state["joints"])
    assert state["faults"]

    reset = client.post("/api/mock/reset")
    assert reset.status_code == 200
    state_after = client.get("/api/state").json()
    assert all(joint["validity"] == "valid" for joint in state_after["joints"])
    assert state_after["faults"] == []


def test_mock_probe_and_export(client: TestClient) -> None:
    probe = client.post("/api/diagnostics/probe")
    assert probe.status_code == 200
    assert probe.json()["truth"] == "simulated"
    assert len(probe.json()["results"]) == 5

    exported = client.get("/api/events/export.csv")
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert "event_id" in exported.text


def test_hardware_mode_rejects_motion(settings: Settings, clock) -> None:
    hardware = replace(
        settings,
        mode=Mode.HARDWARE,
        db_path=settings.db_path.with_name("hardware.db"),
    )
    app = create_app(settings=hardware, clock=clock)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["mode"] == "hardware"
        assert health["capabilities"]["hardware_motion"] is False

        lease = _acquire(client)
        preview = _preview(client)
        response = _execute(client, lease_id=lease["lease_id"], preview=preview)
        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "GATE_REJECTED"
        assert "hardware_motion_enabled" in body["error"]["details"]["blockers"]

        probe = client.post("/api/diagnostics/probe")
        assert probe.status_code == 503
        assert probe.json()["error"]["code"] == "NOT_IMPLEMENTED"
