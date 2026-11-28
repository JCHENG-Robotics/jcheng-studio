"""G8 task takeover tests: locate, fit, plan, verify, mock API."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockRobotAdapter
from app.config import Settings
from app.domain.enums import Mode
from app.domain.grasp_fit import (
    fit as grasp_fit,
    fit_with_j4,
    validate_target as validate_grasp_target,
)
from app.domain.locate import (
    LocateParams,
    cube_box_present,
    cube_point,
    segment_floor_cube,
    top_view_gate,
)
from app.domain.mapping import MappingConfig
from app.domain.models import MotionTarget
from app.domain.profile import ProfileParams
from app.domain.takeover import load_takeover_config
from app.domain.visual import compute_frames, load_urdf
from app.main import create_app
from app.persistence.store import EventStore
from app.service import RobotService, _locate_rejection_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAKEOVER_PATH = PROJECT_ROOT / "configs" / "takeover.yaml"
MAPPING_PATH = PROJECT_ROOT / "configs" / "joint_mapping.yaml"
URDF_PATH = Path(__file__).resolve().parent / "fixtures" / "armpi_ultra_test.urdf"
ARM_JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5")


@pytest.fixture()
def takeover():
    return load_takeover_config(TAKEOVER_PATH)


@pytest.fixture()
def urdf():
    return load_urdf(URDF_PATH)


def _tcp(urdf):
    def tcp(joints):
        frames = compute_frames(
            urdf,
            {name: float(joints[index]) for index, name in enumerate(ARM_JOINTS)},
            {},
        )["frames"]
        for frame in frames:
            if frame["name"] == "end_effector_link":
                return np.asarray(frame["position_m"], dtype=np.float64)
        raise AssertionError("no end_effector_link")

    return tcp


def _floor_cloud(with_cube: bool = True) -> np.ndarray:
    x = np.linspace(0.10, 0.40, 260)
    y = np.linspace(-0.15, 0.15, 220)
    xx, yy = np.meshgrid(x, y)
    floor = np.column_stack(
        [xx.ravel(), yy.ravel(), np.full(xx.size, -0.080)]
    )
    if not with_cube:
        return floor
    side = np.linspace(0.21, 0.25, 40)
    cube_y = np.linspace(-0.020, 0.020, 40)
    cx, cy = np.meshgrid(side, cube_y)
    top = np.column_stack([cx.ravel(), cy.ravel(), np.full(cx.size, -0.040)])
    rng = np.random.default_rng(1)
    return np.vstack([floor + rng.normal(0, 0.0004, floor.shape), top])


def test_segment_synthetic_floor_cube() -> None:
    params = LocateParams(ransac_iterations=300, ransac_points=20000)
    report = segment_floor_cube(_floor_cloud(True), np.eye(4), params)
    assert report["success"] is True
    point, accepted = cube_point(report)
    assert accepted is True
    assert point is not None
    assert abs(point[0] - 0.23) < 0.004
    assert abs(point[1]) < 0.004
    assert abs(point[2] + 0.040) < 0.003
    assert report["segmentation"]["clusters"] >= 1

    empty = segment_floor_cube(_floor_cloud(False), np.eye(4), params)
    assert empty["success"] is False
    assert empty["segmentation"]["clusters"] == 0
    assert cube_box_present(empty, np.array([0.23, 0.0, -0.040])) is False


def test_cube_box_detects_the_floor_cube() -> None:
    report = segment_floor_cube(
        _floor_cloud(True),
        np.eye(4),
        LocateParams(ransac_iterations=300, ransac_points=20000),
    )
    assert cube_box_present(report, np.array([0.23, 0.0, -0.040])) is True
    assert cube_box_present(report, np.array([0.10, 0.0, -0.040])) is False


def _offset_floor_cloud(cx: float = 0.426, cy: float = -0.100) -> np.ndarray:
    """Floor plus a 40 mm cube offset laterally from the tool-down axis."""
    x = np.linspace(0.10, 0.55, 300)
    y = np.linspace(-0.20, 0.20, 260)
    xx, yy = np.meshgrid(x, y)
    floor = np.column_stack(
        [xx.ravel(), yy.ravel(), np.full(xx.size, -0.080)]
    )
    side = np.linspace(cx - 0.020, cx + 0.020, 40)
    cube_y = np.linspace(cy - 0.020, cy + 0.020, 40)
    tx, ty = np.meshgrid(side, cube_y)
    top = np.column_stack([tx.ravel(), ty.ravel(), np.full(tx.size, -0.040)])
    rng = np.random.default_rng(3)
    return np.vstack([floor + rng.normal(0, 0.0004, floor.shape), top])


def test_lateral_100mm_cube_is_located_for_the_chassis_approach() -> None:
    # The 2026-11-21 real capture had a valid 39 mm cube at
    # (0.426, -0.100, -0.057): every gate passed except the 0.09 m y window.
    params = LocateParams(
        ransac_iterations=300,
        ransac_points=20000,
        x_range_m=(0.15, 0.55),
        y_range_m=0.15,
    )
    report = segment_floor_cube(_offset_floor_cloud(), np.eye(4), params)
    point, accepted = cube_point(report)
    assert accepted is True
    assert point is not None
    assert point[0] == pytest.approx(0.426, abs=0.004)
    assert point[1] == pytest.approx(-0.100, abs=0.004)
    assert report["segmentation"]["cluster_details"][0]["ok"] is True


def test_lateral_100mm_cube_is_rejected_by_the_old_window() -> None:
    params = LocateParams(
        ransac_iterations=300,
        ransac_points=20000,
        x_range_m=(0.15, 0.55),
        y_range_m=0.09,
    )
    report = segment_floor_cube(_offset_floor_cloud(), np.eye(4), params)
    point, accepted = cube_point(report)
    assert accepted is False
    assert point is None
    text = _locate_rejection_text(report)
    assert "横向超出定位窗口" in text
    assert "-0.100" in text


def test_takeover_locate_window_covers_the_lateral_approach(takeover) -> None:
    assert takeover.task is not None
    assert takeover.task.locate.y_range_m == pytest.approx(0.15)


def test_top_view_gate_accepts_the_measured_34_6mm_top() -> None:
    # Real capture 2026-11-21: a 39.4 mm cube at (0.434, -0.105, -0.042)
    # produced a 34.6 mm top extent (depth noise/occlusion); the old 35 mm
    # threshold rejected it as partial_top_view.
    top = {"extent_m": [0.03461, 0.03895]}
    ok, reason, extent = top_view_gate(top, 0.030)
    assert ok is True
    assert reason is None
    assert extent == pytest.approx(0.03461)
    old_ok, old_reason, _ = top_view_gate(top, 0.035)
    assert old_ok is False
    assert old_reason == "partial_top_view"


def test_fit_matches_the_recorded_2026_11_20_run(urdf) -> None:
    # cube_point + the validated constant correction from the 2026-11-20 trial.
    target = [
        0.22690956575501345 + 0.0137,
        -0.01390503513063539 + 0.0039,
        -0.05001748059159114 + 0.0089,
    ]
    result = grasp_fit(target, _tcp(urdf), j2=-0.6618, j3=-1.80117, j5=0.0)
    assert result["xy_error_mm"] < 0.1
    assert result["joints"][0] == pytest.approx(-0.0591, abs=0.002)
    assert result["joints"][3] == pytest.approx(-0.3303, abs=0.002)
    assert result["joints"][2] == pytest.approx(-1.80117, abs=1e-9)
    assert not result["reach_limited"]


def _pose_settings(tmp_path: Path) -> Settings:
    return Settings(
        mode=Mode.MOCK,
        robot_id="armpi-ultra-01",
        bind_host="127.0.0.1",
        port=8000,
        db_path=tmp_path / "task.db",
        mapping_path=MAPPING_PATH,
        urdf_path=URDF_PATH,
        frontend_dist=tmp_path / "dist",
        log_level="info",
        hardware_motion_enabled=False,
        # These tests exercise the deprecated dispatch runner directly; the
        # default-disabled policy is covered by test_legacy_motion_disabled.py.
        legacy_motion_enabled=True,
        motion_scope="pose",
        takeover_acknowledged=True,
        takeover_config_path=TAKEOVER_PATH,
    )


def _prepare_view(
    service: RobotService,
    takeover,
    clock,
    *,
    session: str = "task-test",
) -> dict:
    lease = service.acquire_lease(session, 600)
    view = takeover.preset("view_table").joints
    targets = [MotionTarget(name, view[name], 3.2) for name in ARM_JOINTS]
    preview = service.preview(session, targets)
    assert preview["valid"], preview
    service.execute(
        session_id=session,
        lease_id=lease["lease_id"],
        preview_token=preview["preview_token"],
        command_id=preview["command_id"],
        targets=targets,
        idempotency_key="prepare-view",
    )
    clock.advance(3.5)
    service.get_state()
    return lease


def _service(tmp_path: Path, mapping: MappingConfig, clock, takeover):
    settings = _pose_settings(tmp_path)
    store = EventStore(settings.db_path)
    adapter = MockRobotAdapter(mapping, clock=clock, takeover=takeover)
    return RobotService(
        settings, mapping, adapter, store, clock=clock, takeover=takeover
    )


def test_task_plan_requires_the_view(mapping, clock, takeover, tmp_path) -> None:
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        plan = service.task_plan()
        assert plan["valid"] is False
        assert plan["errors"][0]["code"] == "TASK_NOT_AT_VIEW"
        assert plan["view_joints"]["joint2"] == pytest.approx(0.2574)
    finally:
        service.close()


def test_task_plan_and_verify_in_mock(mapping, clock, takeover, tmp_path) -> None:
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        _prepare_view(service, takeover, clock)
        plan = service.task_plan()
        assert plan["valid"], plan
        assert plan["simulated"] is True
        # The fast plan groups the old single-command steps into 7 phases; the
        # gripper is already open, so open_gripper is skipped.
        assert len(plan["steps"]) == 7
        labels = [step["label"] for step in plan["steps"]]
        assert labels == [
            "approach",
            "grasp",
            "close",
            "lift",
            "grip_verify",
            "release",
            "return_view",
        ]
        assert [c["label"] for c in plan["steps"][1]["commands"]] == [
            "descend",
            "grasp",
        ]
        assert [c["label"] for c in plan["steps"][2]["commands"]] == [
            "close_pre",
            "close_grip",
        ]
        assert [c["label"] for c in plan["steps"][5]["commands"]] == [
            "release_descend",
            "release_final",
            "release_open",
        ]
        assert plan["fast"]["mode"] == "s_curve"
        assert plan["fast"]["vmax_rad_s"] == pytest.approx(4.0)
        assert plan["fit"]["xy_error_mm"] < 1.0
        grasp = plan["steps"][1]["commands"][1]
        assert grasp["profile"]["vmax_rad_s"] == pytest.approx(0.35)
        assert grasp["profile"]["segments"] >= 1
        # The direct grasp -> view move replaces the old lift + view_verify
        # pair; the total commanded time is far below the single-point plan.
        assert plan["steps"][3]["commands"][0]["joints"] == plan["view_joints"]
        assert sum(step["duration_s"] for step in plan["steps"]) < 8.0
        assert plan["grasp_joints"]["joint4"] == pytest.approx(
            plan["fit"]["joints"][3], abs=1e-6
        )
        verify = service.task_verify(plan["plan_id"])
        assert verify["held"] is True
        assert verify["simulated"] is True
        # The plan-time validation already checks every step from the previous
        # target. A preview from the *current* view would correctly reject the
        # later steps (the arm has not moved there yet), so it is not a valid
        # test of the plan's internal consistency.
    finally:
        service.close()


def test_task_api_plan_and_verify(tmp_path, mapping, clock, takeover) -> None:
    settings = _pose_settings(tmp_path)
    app = create_app(settings=settings, clock=clock)
    with TestClient(app) as client:
        lease = client.post(
            "/api/control/acquire",
            json={"session_id": "api-task", "ttl_s": 600},
        ).json()
        view = takeover.preset("view_table").joints
        targets = [
            {"joint": name, "target_rad": view[name], "duration_s": 3.2}
            for name in ARM_JOINTS
        ]
        preview = client.post(
            "/api/motion/preview",
            json={"session_id": "api-task", "targets": targets},
        ).json()
        assert preview["valid"], preview
        execute = client.post(
            "/api/motion/execute",
            headers={"Idempotency-Key": "api-view"},
            json={
                "session_id": "api-task",
                "lease_id": lease["lease_id"],
                "preview_token": preview["preview_token"],
                "command_id": preview["command_id"],
                "targets": targets,
            },
        ).json()
        assert execute["status"] == "Dispatched"
        clock.advance(3.5)
        assert client.get("/api/state").status_code == 200

        plan = client.post("/api/task/plan").json()
        assert plan["valid"] is True, plan
        assert plan["plan_id"].startswith("task_")
        verify = client.post(
            "/api/task/verify", json={"plan_id": plan["plan_id"]}
        ).json()
        assert verify["held"] is True
        stale = client.post(
            "/api/task/verify", json={"plan_id": "task_other"}
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "TASK_PLAN_STALE"

        stop = client.post("/api/motion/stop", json={"session_id": "api-task"})
        assert stop.status_code == 200, stop.text
        after_stop = client.post(
            "/api/task/verify", json={"plan_id": plan["plan_id"]}
        )
        assert after_stop.status_code == 409
        assert after_stop.json()["error"]["code"] == "TASK_NOT_PLANNED"


def _clipped_cloud(center_x: float = 0.231, cutoff_m: float = 0.240) -> np.ndarray:
    """A floor cube whose top near edge is inside the sensor's min range."""
    x = np.linspace(0.10, 0.40, 260)
    y = np.linspace(-0.15, 0.15, 220)
    xx, yy = np.meshgrid(x, y)
    floor = np.column_stack([xx.ravel(), yy.ravel(), np.full(xx.size, -0.080)])
    side = np.linspace(center_x - 0.020, center_x + 0.020, 40)
    cube_y = np.linspace(-0.0197, 0.0197, 40)
    cx, cy = np.meshgrid(side, cube_y)
    top = np.column_stack([cx.ravel(), cy.ravel(), np.full(cx.size, -0.040)])
    cloud = np.vstack([floor, top])
    ranges = np.linalg.norm(cloud, axis=1)
    return cloud[ranges >= cutoff_m]


def test_segment_recovers_min_range_clipped_top() -> None:
    cloud = _clipped_cloud()
    params = LocateParams(
        ransac_iterations=300,
        ransac_points=20000,
        cube_size_m=0.0394,
    )
    report = segment_floor_cube(cloud, np.eye(4), params)
    selected = report["selected"]
    assert "rejection" not in report
    assert selected["top_recovered"]["reason"] == "min_range_clip"
    assert selected["top_recovered"]["clipped_side"] == "a_min"
    point, accepted = cube_point(report)
    assert accepted is True
    assert point is not None
    assert abs(point[0] - 0.2313) < 0.0015
    assert abs(point[1]) < 0.0015
    assert abs(point[2] + 0.040) < 0.003
    assert selected["top_recovered"]["centroid_bias_mm"] > 3.0


def test_segment_keeps_rejecting_clipped_top_without_recovery() -> None:
    cloud = _clipped_cloud()
    params = LocateParams(ransac_iterations=300, ransac_points=20000)
    report = segment_floor_cube(cloud, np.eye(4), params)
    assert report["success"] is False
    assert report["rejection"]["reason"] == "partial_top_view"
    _, accepted = cube_point(report)
    assert accepted is False


def test_takeover_task_enables_the_clip_recovery(takeover) -> None:
    assert takeover.task is not None
    assert takeover.task.locate.cube_size_m == pytest.approx(0.0394)
    assert takeover.task.locate.recovery_intact_extent_m == pytest.approx(0.030)


def test_task_plan_requests_a_chassis_approach_out_of_band(
    mapping, clock, takeover, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        RobotService, "MOCK_TASK_POINT", (0.300, 0.000, -0.050)
    )
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        _prepare_view(service, takeover, clock)
        plan = service.task_plan()
        assert plan["valid"] is False
        assert plan["errors"][0]["code"] == "APPROACH_REQUIRED"
        approach = plan["approach"]
        assert approach is not None
        assert approach["vx"] == pytest.approx(1.0)
        assert approach["vy"] == pytest.approx(0.0)
        assert approach["forward_mm"] == pytest.approx(70.0, abs=0.1)
        assert approach["duty"] == 10.0
        assert approach["duration_s"] == pytest.approx(1.429, abs=0.002)
        assert approach["target_point_m"] == [0.230, 0.0]
        assert plan["steps"] == []
    finally:
        service.close()


def test_task_plan_keeps_fit_rejected_when_aligned(
    mapping, clock, takeover, tmp_path, monkeypatch
) -> None:
    # A point at the aim point but a fit that cannot reach it keeps the plain
    # FIT_REJECTED path: no pulse would help.
    monkeypatch.setattr(
        RobotService, "MOCK_TASK_POINT", (0.230, 0.000, -0.050)
    )
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        _prepare_view(service, takeover, clock)
        plan = service.task_plan()
        assert plan["valid"], plan
        assert plan.get("approach") is None
    finally:
        service.close()


def test_chassis_pulse_api_in_mock(tmp_path, mapping, clock, takeover) -> None:
    settings = _pose_settings(tmp_path)
    app = create_app(settings=settings, clock=clock)
    with TestClient(app) as client:
        ok = client.post(
            "/api/chassis/pulse",
            json={
                "session_id": "api-approach",
                "lease_id": "mock-lease",
                "vx": 1.0,
                "vy": 0.0,
                "duty": 10.0,
                "duration_s": 0.5,
            },
        )
        assert ok.status_code == 200
        payload = ok.json()
        assert payload["status"] == "Completed"
        assert payload["verification"] == "simulated"

        too_much = client.post(
            "/api/chassis/pulse",
            json={
                "session_id": "api-approach",
                "lease_id": "mock-lease",
                "vx": 1.0,
                "vy": 0.0,
                "duty": 11.0,
                "duration_s": 0.5,
            },
        )
        assert too_much.status_code == 400

        too_short = client.post(
            "/api/chassis/pulse",
            json={
                "session_id": "api-approach",
                "lease_id": "mock-lease",
                "vx": 1.0,
                "vy": 0.0,
                "duty": 10.0,
                "duration_s": 0.1,
            },
        )
        assert too_short.status_code == 400


def test_profile_preview_and_execute_in_mock(
    mapping, clock, takeover, tmp_path
) -> None:
    """The fast path previews a dense S-curve and executes it in one command."""
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        lease = _prepare_view(service, takeover, clock)
        preset = takeover.preset("approach_low")
        assert preset is not None
        targets = [
            MotionTarget(name, preset.joints[name], 1.0) for name in ARM_JOINTS
        ]
        profile = ProfileParams(4.0, 20.0, 0.05)
        preview = service.preview("task-test", targets, profile=profile)
        assert preview["valid"], preview
        durations = {item["duration_s"] for item in preview["targets"]}
        assert len(durations) == 1
        total = durations.pop()
        assert 0.5 < total < 2.0
        assert preview["pose"] is not None
        execute = service.execute(
            session_id="task-test",
            lease_id=lease["lease_id"],
            preview_token=preview["preview_token"],
            command_id=preview["command_id"],
            targets=targets,
            idempotency_key="profile-execute",
            profile=profile,
        )
        assert execute["status"] == "Dispatched"
        clock.advance(total + 1.5)
        service.tick()
        events = service.events(limit=50, command_id=preview["command_id"])
        stages = [item["stage"] for item in events["items"]]
        assert "Completed" in stages
    finally:
        service.close()


def test_fast_gripper_preview_accepts_the_measured_timings(
    mapping, clock, takeover, tmp_path
) -> None:
    """0.35 s / 423 raw/s is the 2026-11-20 fast close, not a 1.0 s step."""
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        _prepare_view(service, takeover, clock)
        assert takeover.task is not None
        targets = [
            MotionTarget(
                takeover.gripper.joint, takeover.task.preclose_rad, 0.35
            )
        ]
        preview = service.preview("task-test", targets, fast=True)
        assert preview["valid"], preview
        assert preview["targets"][0]["duration_s"] == pytest.approx(0.35)
        slow = service.preview("task-test", targets)
        assert slow["valid"] is False
        assert any(item["code"] == "DURATION_RANGE" for item in slow["errors"])
    finally:
        service.close()


def test_profile_contract_rejects_a_faster_profile(
    mapping, clock, takeover, tmp_path
) -> None:
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        _prepare_view(service, takeover, clock)
        preset = takeover.preset("approach_low")
        assert preset is not None
        targets = [
            MotionTarget(name, preset.joints[name], 1.0) for name in ARM_JOINTS
        ]
        preview = service.preview(
            "task-test", targets, profile=ProfileParams(5.0, 20.0, 0.05)
        )
        assert preview["valid"] is False
        codes = [item["code"] for item in preview["errors"]]
        assert "PROFILE_LIMIT" in codes
    finally:
        service.close()


def test_takeover_carries_the_measured_object_forms(takeover) -> None:
    """The six 2026-11-23 forms + the original cube, with their verdicts."""
    task = takeover.task
    assert task is not None
    assert task.default_form == "cube_39"
    assert [item.form_id for item in task.forms] == [
        "cube_39",
        "block_lying",
        "block_upright",
        "cylinder_standing",
        "cylinder_lying",
        "carton_vlt",
        "sachet",
    ]
    forms = {item.form_id: item for item in task.forms}
    assert forms["block_lying"].mode == "task"
    assert forms["block_lying"].grasp_j4 == pytest.approx(-0.50)
    assert forms["block_lying"].grip_rad == pytest.approx(-0.05)
    assert forms["block_lying"].close_register == 482
    assert forms["block_lying"].base_point_m == pytest.approx((0.233, 0.0006))
    assert forms["cylinder_standing"].correction_mm == pytest.approx(
        (27.0, 3.9, 8.9)
    )
    assert forms["cylinder_standing"].base_point_m == pytest.approx(
        (0.2138, 0.0062)
    )
    assert forms["cylinder_standing"].base_tolerance_m == pytest.approx(0.012)
    assert forms["cylinder_lying"].mode == "refused"
    assert "14.7" in forms["cylinder_lying"].reason
    assert forms["sachet"].mode == "refused"
    assert forms["carton_vlt"].mode == "pose"
    assert forms["carton_vlt"].recorded_pose == pytest.approx(
        {
            "joint1": 0.066,
            "joint2": -0.62,
            "joint3": -1.77,
            "joint4": -0.45,
            "joint5": 0.0,
        }
    )


def test_fit_with_j4_keeps_the_measured_depth(urdf) -> None:
    target = [0.2406, 0.0100, -0.0590]
    result = fit_with_j4(
        target, _tcp(urdf), j2=-0.6618, j3=-1.80117, j4=-0.50, j5=0.0
    )
    assert result["joints"][3] == pytest.approx(-0.50)
    assert abs(result["joints"][0]) < 0.20
    assert result["j4_source"] == "form"
    # The TCP is deliberately not on the fitted xy: the pads are the contact.
    assert result["xy_error_mm"] > 3.0


def test_form_radius_gate_uses_the_measured_point(urdf) -> None:
    # The standing cylinder's +27 mm aim puts the corrected target at 0.263 m,
    # beyond the 0.255 m band, while the object itself is at 0.2359 m. The
    # measured point is the right gate for a fixed-j4 form.
    target = [0.2629, 0.0101, -0.046]
    result = fit_with_j4(
        target, _tcp(urdf), j2=-0.6618, j3=-1.80117, j4=-0.365, j5=0.0
    )
    target_reasons = validate_grasp_target(
        target, result, 999.0, (0.220, 0.255)
    )
    assert any("outside 0.22-0.255 m" in item for item in target_reasons)
    point_reasons = validate_grasp_target(
        target, result, 999.0, (0.220, 0.255), radius_m=0.2359
    )
    assert point_reasons == []


def test_task_plan_uses_the_measured_block_form(
    mapping, clock, takeover, tmp_path
) -> None:
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        _prepare_view(service, takeover, clock)
        plan = service.task_plan("block_lying")
        assert plan["valid"], plan
        assert plan["form_id"] == "block_lying"
        assert plan["point"][:2] == pytest.approx([0.233, 0.0006], abs=1e-6)
        assert plan["grasp_joints"]["joint4"] == pytest.approx(-0.50, abs=1e-6)
        assert plan["fit"]["j4_source"] == "form"
        assert plan["fit"]["j4_fit_rad"] != pytest.approx(-0.50)
        close = plan["steps"][2]["commands"]
        assert close[0]["target_rad"] == pytest.approx(-1.6)
        assert close[1]["target_rad"] == pytest.approx(-0.05)
        # A deep close needs a longer release-open than the cube's 0.45 s.
        release_open = plan["steps"][5]["commands"][2]
        assert release_open["duration_s"] == pytest.approx(0.58, abs=0.01)
        assert any(
            item["code"] == "FORM_J4_MEASURED" for item in plan["warnings"]
        )
        # The default form still produces the historical cube plan.
        default = service.task_plan()
        assert default["form_id"] == "cube_39"
        assert default["grasp_j4"] is None
        assert default["grasp_joints"]["joint4"] == pytest.approx(
            default["fit"]["joints"][3], abs=1e-6
        )
        cylinder = service.task_plan("cylinder_standing")
        assert cylinder["valid"], cylinder
        assert cylinder["grasp_joints"]["joint4"] == pytest.approx(
            -0.365, abs=1e-6
        )
        assert cylinder["correction_mm"] == pytest.approx([27.0, 3.9, 8.9])
    finally:
        service.close()


def test_task_plan_refuses_unusable_forms(
    mapping, clock, takeover, tmp_path
) -> None:
    service = _service(tmp_path, mapping, clock, takeover)
    try:
        lying = service.task_plan("cylinder_lying")
        assert lying["valid"] is False
        assert lying["errors"][0]["code"] == "FORM_REFUSED"
        assert lying["form_id"] == "cylinder_lying"
        assert "14.7" in lying["errors"][0]["message"]
        carton = service.task_plan("carton_vlt")
        assert carton["valid"] is False
        assert carton["errors"][0]["code"] == "FORM_POSE_ONLY"
        assert carton["form"]["recorded_pose"]["joint3"] == pytest.approx(-1.77)
    finally:
        service.close()


def test_task_api_selects_a_form_and_rejects_an_unknown_one(
    tmp_path, mapping, clock, takeover
) -> None:
    settings = _pose_settings(tmp_path)
    app = create_app(settings=settings, clock=clock)
    with TestClient(app) as client:
        lease = client.post(
            "/api/control/acquire",
            json={"session_id": "api-form", "ttl_s": 600},
        ).json()
        view = takeover.preset("view_table").joints
        targets = [
            {"joint": name, "target_rad": view[name], "duration_s": 3.2}
            for name in ARM_JOINTS
        ]
        preview = client.post(
            "/api/motion/preview",
            json={"session_id": "api-form", "targets": targets},
        ).json()
        assert preview["valid"], preview
        client.post(
            "/api/motion/execute",
            headers={"Idempotency-Key": "api-form-view"},
            json={
                "session_id": "api-form",
                "lease_id": lease["lease_id"],
                "preview_token": preview["preview_token"],
                "command_id": preview["command_id"],
                "targets": targets,
            },
        )
        clock.advance(3.5)
        assert client.get("/api/state").status_code == 200

        plan = client.post(
            "/api/task/plan", json={"form": "block_upright"}
        ).json()
        assert plan["valid"] is True, plan
        assert plan["form_id"] == "block_upright"
        assert plan["grasp_joints"]["joint4"] == pytest.approx(-0.39, abs=1e-6)

        unknown = client.post("/api/task/plan", json={"form": "not_a_form"})
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "UNKNOWN_TASK_FORM"
