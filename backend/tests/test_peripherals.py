from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import Mode
from app.main import create_app


def test_mock_peripherals_are_simulated_and_available(client: TestClient) -> None:
    response = client.get("/api/peripherals")
    assert response.status_code == 200, response.text
    payload = response.json()
    battery = payload["battery"]
    camera = payload["camera"]
    aux_camera = payload["aux_camera"]

    assert battery["available"] is True
    assert battery["truth"] == "simulated"
    assert 6400 <= battery["voltage_mv"] <= 8400
    assert battery["cell_count"] == 2
    assert battery["status"] in {"ok", "low", "critical"}

    assert camera["available"] is True
    assert camera["truth"] == "simulated"
    assert camera["width"] == 640
    assert camera["height"] == 400
    assert camera["encoding"] == "bgr8"
    assert camera["fps"] == 8.0

    assert aux_camera["available"] is True
    assert aux_camera["truth"] == "simulated"
    assert aux_camera["topic"] == "mock://aux-camera"
    assert aux_camera["width"] == 640
    assert aux_camera["height"] == 480
    assert aux_camera["encoding"] == "yuyv"
    assert aux_camera["fps"] == 10.0


def test_mock_camera_snapshot_is_labelled_svg(client: TestClient) -> None:
    response = client.get("/api/peripherals/camera/snapshot")
    assert response.status_code == 200, response.text
    assert "image/svg+xml" in response.headers["content-type"]
    assert b"MOCK CAMERA" in response.content
    assert b"SIMULATION" in response.content


def test_mock_aux_camera_snapshot_is_labelled_svg(client: TestClient) -> None:
    response = client.get("/api/peripherals/aux-camera/snapshot")
    assert response.status_code == 200, response.text
    assert "image/svg+xml" in response.headers["content-type"]
    assert b"MOCK AUX CAMERA" in response.content
    assert b"SIMULATION" in response.content
    assert b"mock aux camera" in response.content


def test_battery_low_fault_changes_status(client: TestClient) -> None:
    assert client.post("/api/mock/faults", json={"kind": "battery_low"}).status_code == 200
    battery = client.get("/api/peripherals").json()["battery"]
    assert battery["status"] in {"low", "critical"}
    assert battery["voltage_mv"] < 7200

    assert client.post("/api/mock/reset").status_code == 200
    assert client.get("/api/peripherals").json()["battery"]["status"] == "ok"


def test_camera_offline_fault_makes_snapshot_unavailable(client: TestClient) -> None:
    assert client.post("/api/mock/faults", json={"kind": "camera_offline"}).status_code == 200
    camera = client.get("/api/peripherals").json()["camera"]
    assert camera["available"] is False
    assert camera["validity"] == "unavailable"
    aux_camera = client.get("/api/peripherals").json()["aux_camera"]
    assert aux_camera["available"] is False
    assert aux_camera["validity"] == "unavailable"

    snapshot = client.get("/api/peripherals/camera/snapshot")
    assert snapshot.status_code == 503
    assert snapshot.json()["error"]["code"] == "NOT_IMPLEMENTED"

    aux_snapshot = client.get("/api/peripherals/aux-camera/snapshot")
    assert aux_snapshot.status_code == 503
    assert aux_snapshot.json()["error"]["code"] == "NOT_IMPLEMENTED"

    assert client.post("/api/mock/reset").status_code == 200
    assert client.get("/api/peripherals").json()["camera"]["available"] is True
    assert client.get("/api/peripherals").json()["aux_camera"]["available"] is True


def test_hardware_without_gateway_is_explicitly_unavailable(settings: Settings, clock) -> None:
    hardware = replace(
        settings,
        mode=Mode.HARDWARE,
        db_path=settings.db_path.with_name("hardware-peripherals.db"),
    )
    app = create_app(settings=hardware, clock=clock)
    with TestClient(app) as client:
        payload = client.get("/api/peripherals").json()
        assert payload["battery"]["available"] is False
        assert payload["battery"]["validity"] == "unavailable"
        assert payload["camera"]["available"] is False
        assert payload["camera"]["validity"] == "unavailable"
        assert payload["aux_camera"]["available"] is False
        assert payload["aux_camera"]["validity"] == "unavailable"

        snapshot = client.get("/api/peripherals/camera/snapshot")
        assert snapshot.status_code == 503
        assert snapshot.json()["error"]["code"] == "NOT_IMPLEMENTED"

        aux_snapshot = client.get("/api/peripherals/aux-camera/snapshot")
        assert aux_snapshot.status_code == 503
        assert aux_snapshot.json()["error"]["code"] == "NOT_IMPLEMENTED"
