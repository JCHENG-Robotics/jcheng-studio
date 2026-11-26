from __future__ import annotations

from typing import Any

import pytest

from app.adapters.gateway import GatewayError
from app.adapters.hardware import HardwareAdapter
from app.config import Settings
from app.domain.mapping import MappingConfig


class FakeGateway:
    """Minimal motion/read gateway; records every path it is asked for."""

    base_url = "http://motion"
    token = "token"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        self.calls.append(path)
        if path == "/battery":
            return {
                "available": True,
                "voltage_mv": 7500,
                "truth": "measured",
                "validity": "valid",
                "sample_age_ms": 5,
            }
        raise GatewayError(f"unexpected GET {path}")

    def get_bytes(self, path: str) -> tuple[bytes, str]:
        self.calls.append(path)
        raise GatewayError(f"unexpected GET {path}")


class FakeCameraGateway:
    """Camera-only gateway; the motion gateway must not serve these paths."""

    base_url = "http://camera"
    token = "token"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: GatewayError | None = None

    def get_json(self, path: str) -> dict[str, Any]:
        self.calls.append(path)
        if self.fail is not None:
            raise self.fail
        if path == "/camera/status":
            return {
                "available": True,
                "width": 640,
                "height": 400,
                "encoding": "bgr8",
                "fps": 12.0,
                "truth": "measured",
                "validity": "valid",
                "topic": "/depth_cam/rgb/image_raw",
                "notes": [],
            }
        if path == "/aux-camera/status":
            return {
                "available": True,
                "width": 640,
                "height": 480,
                "encoding": "yuyv",
                "fps": 30.0,
                "truth": "measured",
                "validity": "valid",
                "topic": "mock://aux-camera",
                "notes": [
                    "aux camera is disabled (--no-aux-camera).",
                    "ROS colour topic from ext_cam_node (single UVC owner); "
                    "depth and IR need librealsense.",
                ],
            }
        raise GatewayError(f"unexpected GET {path}")

    def get_bytes(self, path: str) -> tuple[bytes, str]:
        self.calls.append(path)
        if self.fail is not None:
            raise self.fail
        if path == "/camera/snapshot":
            return b"main-jpeg", "image/jpeg"
        if path == "/aux-camera/snapshot":
            return b"aux-jpeg", "image/jpeg"
        raise GatewayError(f"unexpected GET {path}")


def _adapter(
    mapping: MappingConfig, *, camera_url: str | None = "http://camera"
) -> HardwareAdapter:
    return HardwareAdapter(
        mapping,
        gateway_url="http://motion",
        gateway_token="main-token",
        camera_gateway_url=camera_url,
    )


def test_camera_endpoints_use_the_camera_gateway(mapping: MappingConfig) -> None:
    adapter = _adapter(mapping)
    motion = FakeGateway()
    camera = FakeCameraGateway()
    adapter._gateway = motion
    adapter._camera_gateway = camera

    main = adapter.camera_status()
    aux = adapter.aux_camera_status()

    assert main.available is True
    assert main.width == 640 and main.height == 400
    assert main.topic == "/depth_cam/rgb/image_raw"
    assert aux.available is True
    assert aux.height == 480
    # Known gateway notes stay visible but are shown in Chinese.
    assert aux.notes == [
        "辅助相机已禁用（--no-aux-camera）。",
        "ex_cam_node 提供的 ROS 彩色话题（UVC 唯一持有者）；"
        "深度和 IR 需要 librealsense。",
    ]

    assert adapter.camera_snapshot() == (b"main-jpeg", "image/jpeg")
    assert adapter.aux_camera_snapshot() == (b"aux-jpeg", "image/jpeg")
    assert camera.calls == [
        "/camera/status",
        "/aux-camera/status",
        "/camera/snapshot",
        "/aux-camera/snapshot",
    ]
    assert motion.calls == []

    battery = adapter.battery()
    assert battery.available is True
    assert motion.calls == ["/battery"]


def test_camera_gateway_failure_does_not_break_the_motion_gateway(
    mapping: MappingConfig,
) -> None:
    adapter = _adapter(mapping)
    motion = FakeGateway()
    camera = FakeCameraGateway()
    camera.fail = GatewayError("camera gateway down")
    adapter._gateway = motion
    adapter._camera_gateway = camera

    status = adapter.camera_status()
    assert status.available is False
    assert status.validity.value == "unavailable"
    assert "camera gateway down" in status.notes[0]

    assert adapter.camera_snapshot() is None
    assert adapter.battery().available is True


def test_camera_gateway_defaults_to_the_main_gateway(mapping: MappingConfig) -> None:
    adapter = _adapter(mapping, camera_url=None)
    assert adapter._camera_gateway is adapter._gateway


def test_settings_read_the_camera_gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARMPI_GATEWAY_URL", "http://10.42.0.218:8766")
    monkeypatch.setenv("ARMPI_GATEWAY_TOKEN", "shared-token")
    monkeypatch.setenv("ARMPI_CAMERA_GATEWAY_URL", "http://10.42.0.218:8765")
    monkeypatch.delenv("ARMPI_CAMERA_GATEWAY_TOKEN", raising=False)

    settings = Settings.from_env()
    assert settings.camera_gateway_url == "http://10.42.0.218:8765"
    # Both gateway instances read the same token file by default.
    assert settings.camera_gateway_token == "shared-token"

    monkeypatch.setenv("ARMPI_CAMERA_GATEWAY_TOKEN", "camera-token")
    settings = Settings.from_env()
    assert settings.camera_gateway_token == "camera-token"

    monkeypatch.delenv("ARMPI_CAMERA_GATEWAY_URL")
    settings = Settings.from_env()
    assert settings.camera_gateway_url is None
    assert settings.camera_gateway_token is None
