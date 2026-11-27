"""Capability registry: fixed shape, live status, demo labelling."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.adapters.hardware import HardwareAdapter
from app.application.capability_ops import FEATURE_CATALOG, STATUS_ORDER
from app.config import Settings
from app.domain.enums import Mode
from app.main import create_app
from app.persistence.store import EventStore
from app.service import RobotService


def _service(settings: Settings, mapping, clock, adapter) -> RobotService:
    return RobotService(
        settings,
        mapping,
        adapter,
        EventStore(settings.db_path),
        clock=clock,
    )


def _by_id(payload: dict) -> dict[str, dict]:
    return {feature["id"]: feature for feature in payload["features"]}


def test_catalog_shape_is_fixed(settings, mapping, clock) -> None:
    from app.adapters.mock import MockRobotAdapter

    service = _service(settings, mapping, clock, MockRobotAdapter(mapping, clock=clock))
    payload = service.capabilities()
    ids = [feature["id"] for feature in payload["features"]]
    assert ids == [entry["id"] for entry in FEATURE_CATALOG]
    assert all(feature["status"] in STATUS_ORDER for feature in payload["features"])
    assert sum(payload["summary"].values()) == len(FEATURE_CATALOG)
    assert payload["mode"] == "mock"
    assert payload["demo"] is False


def test_mock_keeps_every_surface_visible(settings, mapping, clock) -> None:
    """Legacy motion is default-disabled, so execution pages are read-only.

    The rows stay in the registry (nothing disappears), but no detail may imply
    that mock mode can dispatch motion.
    """
    from app.adapters.mock import MockRobotAdapter

    service = _service(settings, mapping, clock, MockRobotAdapter(mapping, clock=clock))
    features = _by_id(service.capabilities())
    assert features["joints"]["status"] == "read_only"
    assert features["task"]["status"] == "read_only"
    assert features["motion"]["status"] == "read_only"
    assert "legacy" in features["motion"]["detail"]
    assert features["replay"]["status"] == "available"
    assert features["diagnostics"]["status"] == "available"


def test_hardware_read_only_marks_execution_read_only(settings, mapping, clock) -> None:
    hardware = replace(settings, mode=Mode.HARDWARE, hardware_motion_enabled=False)
    service = _service(
        hardware, mapping, clock, HardwareAdapter(mapping, clock=clock)
    )
    payload = service.capabilities()
    features = _by_id(payload)
    assert payload["demo"] is False
    assert features["joints"]["status"] == "read_only"
    assert features["task"]["status"] == "read_only"
    assert features["motion"]["status"] == "read_only"
    assert features["teleop"]["status"] == "not_configured"
    assert features["gateway_v1"]["status"] == "not_configured"
    assert features["session"]["status"] == "not_configured"
    assert features["system_stop"]["status"] == "not_configured"


def test_demo_flag_is_explicit_and_never_executes(settings, mapping, clock) -> None:
    from app.adapters.mock import MockRobotAdapter

    demo = replace(settings, demo=True)
    service = _service(demo, mapping, clock, MockRobotAdapter(mapping, clock=clock))
    payload = service.capabilities()
    assert payload["demo"] is True
    assert _by_id(payload)["motion"]["status"] == "read_only"
    assert "演示模式" in _by_id(payload)["motion"]["detail"]


def test_demo_requires_mock_mode(monkeypatch) -> None:
    monkeypatch.setenv("ARMPI_MODE", "hardware")
    monkeypatch.setenv("ARMPI_DEMO", "1")
    with pytest.raises(ValueError, match="ARMPI_DEMO=1 requires ARMPI_MODE=mock"):
        Settings.from_env()


def test_api_exposes_every_feature(settings, mapping, clock) -> None:
    app = create_app(settings=replace(settings, demo=True), clock=clock)
    with TestClient(app) as client:
        response = client.get("/api/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["demo"] is True
    assert [feature["id"] for feature in payload["features"]] == [
        entry["id"] for entry in FEATURE_CATALOG
    ]
    health = None
    with TestClient(create_app(settings=replace(settings, demo=True), clock=clock)) as client:
        health = client.get("/api/health").json()
    assert health["demo"] is True
