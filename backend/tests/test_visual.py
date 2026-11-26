from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_VISUAL_MODEL, Settings
from app.domain.visual import (
    VisualModelError,
    compose_tf_frames,
    compute_frames,
    load_urdf,
    load_visual_config,
)
from app.main import create_app

TEST_URDF = Path(__file__).resolve().parent / "fixtures" / "armpi_ultra_test.urdf"

CAMERA_FORWARD = {
    "joint1": 0.0,
    "joint2": 0.419,
    "joint3": -1.257,
    "joint4": -0.709,
    "joint5": 0.0,
}


def _frame(result: dict, name: str) -> dict:
    return next(frame for frame in result["frames"] if frame["name"] == name)


def test_urdf_loads_and_camera_forward_fk() -> None:
    model = load_urdf(TEST_URDF)
    assert model.robot_name == "armpi_ultra"
    assert model.root_link == "base_link"
    assert {"joint1", "joint2", "joint3", "joint4", "joint5", "r_joint"} <= set(
        model.movable_order
    )
    assert {"base_link", "link1", "link5", "gripper_link", "camera_connet_link"} <= set(
        model.links
    )
    assert model.verified is False

    config = load_visual_config(DEFAULT_VISUAL_MODEL)
    assert config["camera"]["available"] is True
    assert config["camera"]["frame"] == "camera_optical_calibrated"
    assert config["camera"]["calibration_version"] == "handeye-tsai-20261117"

    result = compute_frames(model, dict(CAMERA_FORWARD), config)
    mount = _frame(result, "camera_connet_link")["position_m"]
    # SRDF notes for camera_forward: x ~= 0.154 m, z ~= 0.282 m.
    assert mount[0] == pytest.approx(0.154, abs=0.02)
    assert mount[2] == pytest.approx(0.282, abs=0.02)

    optical = _frame(result, "camera_optical_calibrated")
    assert optical["parent"] == "link4"
    assert optical["source"] == "measured_hand_eye"
    assert optical["truth"] == "measured"
    assert optical["is_camera"] is True

    nominal = _frame(result, "camera_optical_frame")
    assert nominal["nominal"] is True
    assert nominal["source"] == "urdf_fk"


def test_mimic_and_missing_state_are_reported() -> None:
    model = load_urdf(TEST_URDF)
    config = load_visual_config(DEFAULT_VISUAL_MODEL)
    result = compute_frames(
        model,
        {**CAMERA_FORWARD, "r_joint": -0.5},
        config,
    )
    assert result["derived_joints"]["l_joint"] == pytest.approx(0.5)


def test_missing_joint_positions_are_explicit() -> None:
    model = load_urdf(TEST_URDF)
    config = load_visual_config(DEFAULT_VISUAL_MODEL)
    result = compute_frames(model, {}, config)
    assert "joint1" in result["missing"]
    assert "joint5" in result["missing"]
    assert result["issues"]


def test_cyclic_urdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cycle.urdf"
    path.write_text(
        """
        <robot name="cycle">
          <link name="a"/>
          <link name="b"/>
          <joint name="j1" type="fixed">
            <parent link="a"/><child link="b"/>
          </joint>
          <joint name="j2" type="fixed">
            <parent link="b"/><child link="a"/>
          </joint>
        </robot>
        """,
        encoding="utf-8",
    )
    with pytest.raises(VisualModelError):
        load_urdf(path)


def test_visual_api_and_camera_reference(client: TestClient) -> None:
    response = client.get("/api/visual")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "mock"
    assert body["source_label"] == "SIMULATION"
    assert body["urdf"]["available"] is True
    assert body["urdf"]["verified"] is False
    assert body["urdf"]["links"] > 10
    assert body["tf"]["status"] == "model_only"
    assert body["tf"]["source"] == "urdf_fk"
    assert body["tf"]["live"] is False
    assert len(body["tf"]["frames"]) > 10
    assert body["tf"]["camera_frame"]["name"] == "camera_optical_calibrated"
    assert body["camera"]["available"] is True
    assert body["camera"]["truth"] == "simulated"
    assert body["camera"]["frame"] == "rgb_camera_link"
    assert body["camera"]["optical_frame"] == "camera_optical_calibrated"
    assert any("暂定" in warning for warning in body["warnings"])
    assert any("模拟" in warning for warning in body["camera"]["warnings"])

    joint_names = {joint["name"] for joint in body["joints"]}
    assert {"joint1", "joint2", "joint3", "joint4", "joint5"} <= joint_names
    assert all(joint["limit_source"] for joint in body["joints"])
    assert body["joints"][0]["position_rad"] is not None

    empty = client.get("/api/visual/camera-references")
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    saved = client.post(
        "/api/visual/camera-references",
        json={"note": "calibration sample before on-site run"},
    )
    assert saved.status_code == 200, saved.text
    reference = saved.json()
    assert reference["truth"] == "simulated"
    assert reference["calibration_version"] == "handeye-tsai-20261117"
    assert reference["tf_status"] == "model_only"
    assert reference["width"] == 640
    assert reference["height"] == 400

    listed = client.get("/api/visual/camera-references")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    events = client.get("/api/events").json()["items"]
    stages = [event["stage"] for event in events]
    assert "CameraReferenceSaved" in stages
    assert "Dispatched" not in stages
    assert "Requested" not in stages

    too_long = client.post(
        "/api/visual/camera-references",
        json={"note": "x" * 501},
    )
    assert too_long.status_code == 422


def test_compose_live_tf_frames() -> None:
    payload = {
        "transforms": [
            {
                "parent": "world",
                "child": "base_link",
                "translation_m": [99.0, 0.0, 0.0],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "age_ms": 1,
                "static": True,
            },
            {
                "parent": "base_link",
                "child": "link1",
                "translation_m": [0.1, 0.0, 0.2],
                "rotation_xyzw": [0.0, 0.0, 0.7071068, 0.7071068],
                "age_ms": 5,
                "static": False,
            },
            {
                "parent": "link1",
                "child": "camera_optical_calibrated",
                "translation_m": [0.0, 0.0, 0.05],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "age_ms": 5,
                "static": False,
            },
        ]
    }
    result = compose_tf_frames(payload)
    frames = {frame["name"]: frame for frame in result["frames"]}
    assert frames["base_link"]["position_m"] == [0, 0, 0]
    assert frames["link1"]["position_m"] == pytest.approx([0.1, 0.0, 0.2])
    assert frames["camera_optical_calibrated"]["position_m"] == pytest.approx(
        [0.1, 0.0, 0.25]
    )
    assert frames["camera_optical_calibrated"]["source"] == "live_tf"
    assert frames["camera_optical_calibrated"]["is_camera"] is True
    assert result["unresolved"] == [{"parent": "world", "child": "base_link"}]


def test_visual_snapshot_prefers_live_tf(service) -> None:
    payload = {
        "available": True,
        "status": "ok",
        "truth": "measured_tf",
        "source": "tf2_topics",
        "dynamic_edges": 2,
        "static_edges": 1,
        "freshest_age_ms": 5,
        "max_age_ms": 5,
        "transforms": [
            {
                "parent": "base_link",
                "child": "link1",
                "translation_m": [0.07, 0.0, 0.071],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "age_ms": 5,
                "static": False,
            },
            {
                "parent": "base_link",
                "child": "camera_optical_calibrated",
                "translation_m": [0.15, 0.01, 0.28],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "age_ms": 5,
                "static": True,
            },
        ],
        "notes": ["test tf snapshot"],
    }
    service.adapter.tf_snapshot = lambda: payload
    snapshot = service.visual_snapshot()
    assert snapshot["tf"]["source"] == "tf2_topics"
    assert snapshot["tf"]["live"] is True
    assert snapshot["tf"]["status"] == "live"
    assert snapshot["tf"]["truth"] == "measured_tf"
    frames = {frame["name"]: frame for frame in snapshot["tf"]["frames"]}
    assert frames["link1"]["source"] == "live_tf"
    assert frames["link1"]["position_m"] == pytest.approx([0.07, 0.0, 0.071])
    assert frames["camera_optical_calibrated"]["source"] == "live_tf"
    assert snapshot["camera"]["tf_status"] == "live"
    assert "camera_optical_calibrated" in snapshot["camera"]["tf_frames"]
    assert any("来自网关的实时 TF 快照" in warning for warning in snapshot["warnings"])


def test_missing_urdf_returns_invalid_not_crash(tmp_path: Path, settings: Settings) -> None:
    app = create_app(
        settings=replace(
            settings,
            urdf_path=tmp_path / "missing.urdf",
            visual_model_path=tmp_path / "missing.yaml",
        )
    )
    with TestClient(app) as client:
        response = client.get("/api/visual")
        assert response.status_code == 200
        body = response.json()
        assert body["urdf"]["available"] is False
        assert body["tf"]["status"] == "invalid"
        assert body["tf"]["frames"] == []
        assert body["camera"]["optical_frame"] == "camera_optical_calibrated"
        assert any("not found" in warning for warning in body["warnings"])
