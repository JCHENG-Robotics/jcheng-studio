"""G7 pose-takeover tests: contract, validation, FK gate, mock execution."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.base import HardwareMotionDisabled
from app.adapters.hardware import HardwareAdapter
from app.adapters.mock import MockRobotAdapter
from app.config import Settings
from app.domain.enums import CommandStage, Mode
from app.domain.mapping import MappingConfig
from app.domain.models import MotionTarget
from app.domain.takeover import TAKEOVER_ACK, load_takeover_config
from app.domain.visual import load_urdf
from app.main import create_app
from app.persistence.store import EventStore
from app.safety.pose_gate import PoseGate
from app.service import RobotService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAKEOVER_PATH = PROJECT_ROOT / "configs" / "takeover.yaml"
URDF_PATH = Path(__file__).resolve().parent / "fixtures" / "armpi_ultra_test.urdf"


@pytest.fixture()
def takeover():
    return load_takeover_config(TAKEOVER_PATH)


@pytest.fixture()
def urdf():
    return load_urdf(URDF_PATH)


@pytest.fixture()
def pose_settings(tmp_path: Path) -> Settings:
    return Settings(
        mode=Mode.MOCK,
        robot_id="armpi-ultra-01",
        bind_host="127.0.0.1",
        port=8000,
        db_path=tmp_path / "pose.db",
        mapping_path=PROJECT_ROOT / "configs" / "joint_mapping.yaml",
        urdf_path=URDF_PATH,
        frontend_dist=tmp_path / "dist",
        log_level="info",
        hardware_motion_enabled=False,
        motion_scope="pose",
        takeover_acknowledged=True,
        takeover_config_path=TAKEOVER_PATH,
    )


@pytest.fixture()
def pose_service(
    pose_settings: Settings,
    mapping: MappingConfig,
    clock,
    takeover,
):
    store = EventStore(pose_settings.db_path)
    adapter = MockRobotAdapter(mapping, clock=clock, takeover=takeover)
    service = RobotService(
        pose_settings, mapping, adapter, store, clock=clock, takeover=takeover
    )
    yield service
    service.close()


def _pose_targets(values: dict[str, float], duration: float) -> list[MotionTarget]:
    return [
        MotionTarget(name, value, duration) for name, value in values.items()
    ]


def _plan(service: RobotService) -> list[MotionTarget]:
    return _pose_targets(
        {
            "joint1": 0.0,
            "joint2": -0.6618,
            "joint3": -1.80117,
            "joint4": -0.6704,
            "joint5": 0.0,
        },
        4.0,
    )


def test_contract_presets_pass_the_static_gate(takeover, urdf) -> None:
    gate = PoseGate(takeover, urdf)
    assert takeover.presets
    for preset in takeover.presets:
        outcome = gate.check_pose(preset.joints)
        assert outcome.ok, (preset.preset_id, outcome.violations)
        assert outcome.tcp_m is not None
        assert outcome.table_clearance_m is not None
        assert outcome.table_clearance_m >= takeover.table_plane.clearance_m


def test_pose_preview_accepts_five_joints(pose_service: RobotService) -> None:
    preview = pose_service.preview("s1", _plan(pose_service))
    assert preview["valid"], preview
    assert len(preview["targets"]) == 5
    assert preview["pose"]["ok"] is True
    assert preview["pose"]["tcp_m"][2] < 0.0


def test_pose_preview_rejects_partial_joint_set(pose_service: RobotService) -> None:
    preview = pose_service.preview(
        "s1",
        _pose_targets({"joint1": 0.0, "joint2": -0.6618}, 4.0),
    )
    assert preview["valid"] is False
    assert preview["errors"][0]["code"] == "POSE_JOINT_SET"


def test_pose_velocity_cap(pose_service: RobotService) -> None:
    preview = pose_service.preview("s1", _pose_targets(
        {
            "joint1": 0.0,
            "joint2": -0.6618,
            "joint3": -1.80117,
            "joint4": -0.6704,
            "joint5": 0.0,
        },
        1.0,
    ))
    assert preview["valid"] is False
    assert any(issue["code"] == "VELOCITY_EXCEEDED" for issue in preview["errors"])


def test_gripper_preview_range_and_speed(pose_service: RobotService, takeover) -> None:
    gripper = takeover.gripper
    valid = pose_service.preview(
        "s1",
        [MotionTarget(gripper.joint, gripper.close_rad, 5.5)],
    )
    assert valid["valid"], valid
    assert valid["targets"][0]["completion"] == "timed"

    fast = pose_service.preview(
        "s1", [MotionTarget(gripper.joint, gripper.close_rad, 1.0)]
    )
    assert fast["valid"] is False
    assert fast["errors"][0]["code"] == "VELOCITY_EXCEEDED"

    out = pose_service.preview(
        "s1", [MotionTarget(gripper.joint, 0.5, 5.5)]
    )
    assert out["valid"] is False
    assert out["errors"][0]["code"] == "GRIPPER_RANGE"


def test_low_pose_yaw_guard_blocks(pose_service: RobotService) -> None:
    """joint1 yaw at a TCP below the measured safe yaw height is rejected."""
    preview = pose_service.preview(
        "s1",
        _pose_targets(
            {
                "joint1": 0.5,
                "joint2": -0.6618,
                "joint3": -1.80117,
                "joint4": 0.0,
                "joint5": 0.0,
            },
            4.0,
        ),
    )
    assert preview["valid"] is False
    assert any(issue["code"] == "LOW_POSE_YAW" for issue in preview["errors"])


def test_gate_rejects_a_tighter_workspace(takeover, urdf) -> None:
    tight = replace(
        takeover,
        workspace=replace(
            takeover.workspace,
            lower=(-0.01, -0.01, 0.5),
            upper=(0.01, 0.01, 0.6),
        ),
    )
    gate = PoseGate(tight, urdf)
    outcome = gate.check_pose(takeover.presets[0].joints)
    assert outcome.ok is False
    assert any(item.code == "WORKSPACE" for item in outcome.violations)


def test_gate_rejects_a_high_table(takeover, urdf) -> None:
    raised = replace(
        takeover,
        table_plane=replace(takeover.table_plane, clearance_m=0.5),
    )
    gate = PoseGate(raised, urdf)
    outcome = gate.check_pose(takeover.preset("grasp_low").joints)
    assert outcome.ok is False
    assert any(item.code == "TABLE_CLEARANCE" for item in outcome.violations)


def test_gate_fails_closed_without_tcp_link(takeover, urdf) -> None:
    joint = urdf.joints.pop("end_effector_joint")
    urdf.children[joint.parent] = [
        item for item in urdf.children[joint.parent] if item.name != joint.name
    ]
    urdf.child_joint.pop("end_effector_link", None)
    gate = PoseGate(takeover, urdf)
    outcome = gate.check_pose(takeover.presets[0].joints)
    assert outcome.ok is False
    assert any(item.code == "FK_UNAVAILABLE" for item in outcome.violations)


def test_mock_pose_execute_completes(pose_service: RobotService, clock) -> None:
    lease = pose_service.acquire_lease("s1", 60)
    targets = _plan(pose_service)
    preview = pose_service.preview("s1", targets)
    assert preview["valid"], preview
    response = pose_service.execute(
        session_id="s1",
        lease_id=lease["lease_id"],
        preview_token=preview["preview_token"],
        command_id=preview["command_id"],
        targets=targets,
        idempotency_key="pose-1",
    )
    assert response["status"] == CommandStage.DISPATCHED.value
    clock.advance(4.5)
    state = pose_service.get_state()
    assert state["connection_state"] == "Simulation"
    events = pose_service.events(limit=50)["items"]
    stages = [event["stage"] for event in events]
    assert "Completed" in stages


def test_presets_endpoint(pose_settings: Settings, clock) -> None:
    with TestClient(create_app(settings=pose_settings, clock=clock)) as client:
        payload = client.get("/api/motion/presets").json()
        assert payload["available"] is True
        assert payload["scope"] == "pose"
        assert len(payload["presets"]) == 5
        assert payload["contract"]["max_velocity_rad_s"] == 0.5
        assert payload["current"]["tcp_m"] is not None
        assert all(preset["limit_ok"] for preset in payload["presets"])
        state = client.get("/api/state").json()
        assert state["gripper"]["joint"] == "r_joint"
        assert state["capabilities"]["motion_scope"] == "pose"
        assert state["capabilities"]["gripper_control"] is True


def test_settings_require_takeover_ack(monkeypatch) -> None:
    monkeypatch.setenv("ARMPI_MODE", "mock")
    monkeypatch.setenv("ARMPI_MOTION_SCOPE", "pose")
    monkeypatch.delenv("ARMPI_TAKEOVER_ACK", raising=False)
    with pytest.raises(ValueError, match="ARMPI_TAKEOVER_ACK"):
        Settings.from_env()

    monkeypatch.setenv("ARMPI_TAKEOVER_ACK", TAKEOVER_ACK)
    settings = Settings.from_env()
    assert settings.motion_scope == "pose"
    assert settings.takeover_acknowledged is True


def test_hardware_pose_requires_a_pose_gateway(mapping: MappingConfig) -> None:
    class FakeGateway:
        base_url = "http://fake"
        token = "t"

        def __init__(self) -> None:
            self.health = {
                "status": "ok",
                "ros_ready": True,
                "motion_enabled": True,
                "motion_mode": "step",
            }

        def get_json(self, path: str) -> dict[str, Any]:
            if path == "/health":
                return dict(self.health)
            if path == "/state":
                joints = {}
                measured = {}
                for name in (
                    "joint1",
                    "joint2",
                    "joint3",
                    "joint4",
                    "joint5",
                    "r_joint",
                ):
                    joints[name] = {
                        "position_rad": 0.0,
                        "target_rad": 0.0,
                        "truth": "commanded",
                        "validity": "valid",
                        "sample_age_ms": 5,
                    }
                    measured[name] = {
                        "position_rad": 0.0,
                        "raw_position": 500 if name != "r_joint" else 170,
                        "truth": "measured",
                        "validity": "valid",
                        "sample_age_ms": 5,
                    }
                return {
                    "joints": joints,
                    "measured": measured,
                    "measured_ready": True,
                    "ros_time_ns": 1,
                }
            raise AssertionError(path)

    takeover = load_takeover_config(TAKEOVER_PATH)
    adapter = HardwareAdapter(
        mapping,
        gateway_url="http://fake",
        gateway_token="t",
        motion_enabled=True,
        motion_scope="pose",
        takeover=takeover,
    )
    adapter._gateway = FakeGateway()
    state = adapter.snapshot()
    assert state.capabilities.hardware_motion is False
    assert any(fault.code == "GATEWAY_SCOPE_MISMATCH" for fault in state.faults)
    with pytest.raises(HardwareMotionDisabled):
        adapter.dispatch([], "cmd")
