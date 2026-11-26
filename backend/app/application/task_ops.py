"""G8 task planning operations, split out of RobotService.

The methods are moved verbatim as a first structural boundary. They still use
the service facade for state, adapter, settings and the task lock; a later
step should inject those dependencies explicitly.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from ..domain.approach import plan_approach
from ..domain.enums import ConnectionState, ErrorCode, Mode, Truth
from ..domain.grasp_fit import (
    fit as grasp_fit,
    fit_with_j4,
    validate_target as validate_grasp_target,
)
from ..domain.locate import cube_box_present, cube_point, segment_floor_cube
from ..domain.models import RobotState, utc_now_iso
from ..domain.task import build_task_plan
from ..domain.takeover import ARM_JOINTS, TakeoverConfig, TaskFormConfig
from ..domain.visual import VisualModelError, compute_frames
from ..errors import ServiceError


_LOCATE_REASON_LABELS = {
    "y_out_of_region": "横向超出定位窗口",
    "x_out_of_region": "前后超出定位范围",
    "too_few_points": "点太少",
    "too_wide": "横向尺寸过大",
    "partial_top_view": "顶面不完整",
}


def _locate_rejection_text(report: dict[str, Any]) -> str:
    """Explain why the segmenter did not accept a cluster."""
    rejection = report.get("rejection")
    if isinstance(rejection, dict) and rejection.get("reason"):
        reason = str(rejection["reason"])
        return _LOCATE_REASON_LABELS.get(reason, reason)
    details = report.get("segmentation", {}).get("cluster_details", [])
    parts: list[str] = []
    for cluster in details:
        if not isinstance(cluster, dict) or cluster.get("ok"):
            continue
        why = [str(item) for item in cluster.get("why", []) if item]
        if not why:
            continue
        readable = [
            _LOCATE_REASON_LABELS.get(item, item) for item in why
        ]
        centroid = (cluster.get("bounds") or {}).get("centroid")
        text = "、".join(readable)
        if centroid:
            text += f"，中心 [{', '.join(f'{float(v):.3f}' for v in centroid)}] m"
        parts.append(text)
    return "；".join(parts[:3]) if parts else "没有可用的木块候选"



class TaskOperations:
    # ---------------------------------------------------------------- G8 task
    MOCK_TASK_POINT = (
        0.22690956575501345,
        -0.01390503513063539,
        -0.05001748059159114,
    )

    def _task_context(self) -> tuple[TakeoverConfig, Any]:
        takeover = self.takeover
        if takeover is None or takeover.task is None:
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                "task takeover requires a takeover contract with a task block",
                status_code=503,
            )
        if self.motion_limits.scope != "pose":
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                "task takeover requires ARMPI_MOTION_SCOPE=pose",
                status_code=503,
            )
        return takeover, takeover.task

    def _task_tcp(self, joints: list[float]) -> "np.ndarray":
        model, _config = self._load_visual_bundle()
        frames = compute_frames(
            model,
            {name: float(joints[index]) for index, name in enumerate(ARM_JOINTS)},
            {},
        )["frames"]
        for frame in frames:
            if frame["name"] == "end_effector_link":
                return np.asarray(frame["position_m"], dtype=np.float64)
        raise VisualModelError(["end_effector_link is missing from the URDF FK"])

    def _task_link4(self, joints: dict[str, float]) -> "np.ndarray":
        model, _config = self._load_visual_bundle()
        frames = compute_frames(model, dict(joints), {})["frames"]
        for frame in frames:
            if frame["name"] == "link4":
                matrix = np.eye(4)
                matrix[:3, :3] = np.asarray(
                    frame["rotation"], dtype=np.float64
                ).reshape(3, 3)
                matrix[:3, 3] = np.asarray(
                    frame["position_m"], dtype=np.float64
                )
                return matrix
        raise VisualModelError(["link4 is missing from the URDF FK"])

    @staticmethod
    def _task_hand_eye(task: Any) -> "np.ndarray":
        path = Path(task.hand_eye_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            matrix = np.asarray(
                data["hand_eye"]["methods"]["TSAI"]["T_link4_camera"],
                dtype=np.float64,
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ServiceError(
                ErrorCode.FK_UNAVAILABLE.value,
                f"hand-eye calibration is unusable: {exc}",
                status_code=503,
            ) from exc
        if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
            raise ServiceError(
                ErrorCode.FK_UNAVAILABLE.value,
                "hand-eye T_link4_camera is not a finite 4x4 matrix",
                status_code=503,
            )
        return matrix

    def _task_invalid(
        self,
        code: str,
        message: str,
        *,
        view_joints: dict[str, float] | None = None,
        locate: dict[str, Any] | None = None,
        fit: dict[str, Any] | None = None,
        current: dict[str, float] | None = None,
        simulated: bool = False,
        extra_errors: list[dict[str, Any]] | None = None,
        approach: dict[str, Any] | None = None,
        form: TaskFormConfig | None = None,
    ) -> dict[str, Any]:
        errors = [
            {"label": "task", "code": code, "message": message, "joint": None}
        ]
        errors.extend(extra_errors or [])
        return {
            "schema_version": 1,
            "valid": False,
            "errors": errors,
            "warnings": [],
            "simulated": simulated,
            "form": form.to_public_dict() if form is not None else None,
            "form_id": form.form_id if form is not None else None,
            "steps": [],
            "view_joints": view_joints or {},
            "current_joints": current or {},
            "locate": locate or {},
            "fit": fit,
            "approach": approach,
            "plan_id": None,
        }

    def _task_pose_basis(
        self,
        state: RobotState,
        view: Any,
    ) -> tuple[dict[str, float], dict[str, int | None], str]:
        """Return the joint basis for the locate FK and its pulse source.

        The vendor command cache at the view can differ from the actual
        registers by ~0.03 rad (~9 mm at 0.3 m). Prefer the gateway's last bus
        read when it is at the same view; otherwise use the cache and warn.
        """
        cache = {
            joint.name: float(joint.position_rad)
            for joint in state.joints
            if joint.position_rad is not None
        }
        raw = {
            joint.name: joint.raw_position for joint in state.joints
        }
        if self.settings.mode is Mode.MOCK:
            return cache, raw, "simulated"
        try:
            actual = self.adapter.last_actual()
        except Exception:  # noqa: BLE001 - the cache remains usable
            actual = None
        if isinstance(actual, dict):
            rad = {}
            for name in ARM_JOINTS:
                item = actual.get(name)
                if not isinstance(item, dict) or item.get("position_rad") is None:
                    rad = {}
                    break
                rad[name] = float(item["position_rad"])
            if rad:
                deviations = {
                    name: abs(rad[name] - float(view.joints[name]))
                    for name in ARM_JOINTS
                }
                if max(deviations.values()) <= 0.12:
                    return (
                        rad,
                        {
                            name: (
                                int(actual[name]["raw_position"])
                                if isinstance(actual[name], dict)
                                and actual[name].get("raw_position") is not None
                                else raw.get(name)
                            )
                            for name in ARM_JOINTS
                        },
                        "measured_on_demand",
                    )
        measured = all(
            joint.truth in (Truth.MEASURED, Truth.MEASURED_ON_DEMAND)
            for joint in state.joints
        )
        return cache, raw, "measured_on_demand" if measured else "commanded"

    def task_plan(self, form_id: str | None = None) -> dict[str, Any]:
        """Locate the object and build the validated task plan.

        Read-only: no motion command is issued. The arm must already be at the
        task view preset; the cloud and joint positions are read at that pose.

        ``form_id`` selects one measured object form from
        ``docs/phase2/OBJECT_GRASP_CATALOG.md`` through ``task.forms`` in the
        takeover contract. ``None`` uses ``task.default_form`` (the original
        39.4 mm cube when the contract has no form list).
        """
        takeover, task = self._task_context()
        state = self._state()
        simulated = self.settings.mode is Mode.MOCK
        requested_form = form_id or task.default_form
        form = task.form(requested_form)
        if form is None:
            raise ServiceError(
                "UNKNOWN_TASK_FORM",
                f"task form {requested_form!r} is not in task.forms",
                status_code=422,
                details={"available": [item.form_id for item in task.forms]},
            )
        view = takeover.preset(task.view_preset)
        if view is None:
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                f"task view preset {task.view_preset!r} is missing",
                status_code=503,
            )
        if state.connection_state is ConnectionState.EXECUTING:
            raise ServiceError(
                ErrorCode.ALREADY_EXECUTING.value,
                "motion is executing; wait for it to finish before locating",
                status_code=409,
            )
        if (
            self.settings.mode is Mode.HARDWARE
            and not state.capabilities.hardware_motion
        ):
            raise ServiceError(
                ErrorCode.HARDWARE_MOTION_DISABLED.value,
                "task takeover requires hardware motion and pose scope",
                status_code=503,
            )
        current = {
            joint.name: joint.position_rad
            for joint in state.joints
            if joint.position_rad is not None
        }
        if set(current) != set(ARM_JOINTS):
            return self._task_invalid(
                "TASK_STATE_UNAVAILABLE",
                "not all five arm joints have a position",
                view_joints=view.joints,
                current=current,
                simulated=simulated,
                form=form,
            )
        if form.mode != "task":
            return self._task_invalid(
                "FORM_POSE_ONLY" if form.mode == "pose" else "FORM_REFUSED",
                f"{form.label}：{form.reason}",
                view_joints=view.joints,
                current=current,
                simulated=simulated,
                form=form,
            )
        offsets = {
            name: abs(float(current[name]) - float(view.joints[name]))
            for name in ARM_JOINTS
        }
        at_view = max(offsets.values()) <= 0.12
        if not at_view:
            return self._task_invalid(
                "TASK_NOT_AT_VIEW",
                "the arm is not at the task view pose; prepare the view first",
                view_joints=view.joints,
                current=current,
                simulated=simulated,
                form=form,
            )
        basis, raw_pulses, source = self._task_pose_basis(state, view)
        if simulated:
            source = "simulated"
        report: dict[str, Any]
        point: "np.ndarray"
        meta: dict[str, Any] = {}
        if simulated:
            source = "simulated"
            # The mock cube point is the 39.4 mm cube. A form with a measured
            # base point is simulated at that base, so the base window and the
            # fixed j4 exercise the same path as hardware.
            if form.base_point_m is not None:
                point = np.asarray(
                    [
                        form.base_point_m[0],
                        form.base_point_m[1],
                        self.MOCK_TASK_POINT[2],
                    ],
                    dtype=np.float64,
                )
            else:
                point = np.asarray(self.MOCK_TASK_POINT, dtype=np.float64)
            report = {
                "schema_version": 1,
                "mode": "simulated_locate",
                "success": True,
                "cloud": {"simulated": True},
                "selected": {
                    "grasp_point_base": [float(value) for value in point],
                    "top": {"centroid": [float(value) for value in point]},
                },
                "segmentation": {"clusters": 0, "cluster_details": []},
            }
        else:
            cloud = self.adapter.pointcloud()
            if cloud is None or cloud.get("points") is None:
                raise ServiceError(
                    ErrorCode.POINTCLOUD_UNAVAILABLE.value,
                    "the gateway has no registered point cloud; start the pose "
                    "gateway with --pointcloud-topic on 8766",
                    status_code=503,
                    details={"cloud": (cloud or {}).get("meta")},
                )
            meta = cloud["meta"] if isinstance(cloud.get("meta"), dict) else {}
            points = cloud["points"]
            age = meta.get("sample_age_ms")
            if isinstance(age, (int, float)) and age > 1500:
                raise ServiceError(
                    ErrorCode.POINTCLOUD_STALE.value,
                    f"the point cloud is {age:.0f} ms old",
                    status_code=409,
                    details={"cloud": meta},
                )
            try:
                hand_eye = self._task_hand_eye(task)
                link4 = self._task_link4(basis)
                report = segment_floor_cube(
                    points,
                    link4 @ hand_eye,
                    task.locate,
                    cloud_meta={
                        "frame_id": meta.get("frame_id"),
                        "topic": meta.get("topic"),
                        "sample_age_ms": age,
                        "layout": meta.get("layout"),
                    },
                )
            except VisualModelError as exc:
                return self._task_invalid(
                    ErrorCode.FK_UNAVAILABLE.value,
                    "URDF FK is unavailable: " + "; ".join(exc.issues),
                    view_joints=view.joints,
                    current=current,
                    simulated=simulated,
                    form=form,
                )
            measured, accepted = cube_point(report)
            if measured is None or not accepted:
                reason = _locate_rejection_text(report)
                target_label = (
                    "木块" if form.form_id == task.default_form else form.label
                )
                return self._task_invalid(
                    "LOCATE_FAILED",
                    f"定位失败：没有可用的{target_label}点（{reason}）",
                    view_joints=view.joints,
                    locate=report,
                    current=current,
                    simulated=simulated,
                    form=form,
                )
            point = measured
        # A catalog form's j4 was measured at a specific object position. If
        # the object is outside that base window, use the G8.1 loop to pulse
        # toward the measured base point instead of the cube's (0.230, 0.0).
        base_gated = (
            form.grasp_j4 is not None
            and form.base_point_m is not None
            and task.approach is not None
        )
        if base_gated:
            base_x, base_y = form.base_point_m
            base_distance = math.hypot(
                float(point[0]) - base_x, float(point[1]) - base_y
            )
            if base_distance > form.base_tolerance_m:
                approach = plan_approach(
                    point,
                    replace(task.approach, target_point_m=form.base_point_m),
                )
                if approach is not None:
                    return self._task_invalid(
                        "APPROACH_REQUIRED",
                        f"{form.label}：物体距目录基准位 "
                        f"({base_x:.3f}, {base_y:.3f}) "
                        f"{base_distance * 1000.0:.0f} mm，超过 "
                        f"{form.base_tolerance_m * 1000.0:.0f} mm 窗口；"
                        "先底盘脉冲到基准位再抓。",
                        view_joints=view.joints,
                        locate=report,
                        current=current,
                        simulated=simulated,
                        approach=approach,
                        form=form,
                    )
        correction_mm = (
            form.correction_mm
            if form.correction_mm is not None
            else task.correction_mm
        )
        correction = np.asarray(correction_mm, dtype=np.float64) / 1000.0
        target = point + correction
        grasp_j2 = (
            form.grasp_j2 if form.grasp_j2 is not None else task.grasp_j2
        )
        grasp_j3 = (
            form.grasp_j3 if form.grasp_j3 is not None else task.grasp_j3
        )
        grasp_j5 = (
            form.grasp_j5 if form.grasp_j5 is not None else task.grasp_j5
        )
        try:
            fit_result = grasp_fit(
                target,
                self._task_tcp,
                j2=grasp_j2,
                j3=grasp_j3,
                j5=grasp_j5,
            )
            if form.grasp_j4 is not None:
                # The catalog pose is a measured wrist depth, not a fit: keep
                # joint4 exactly and solve joint1 on the target azimuth.
                fitted_j4 = float(fit_result["joints"][3])
                fit_result = fit_with_j4(
                    target,
                    self._task_tcp,
                    j2=grasp_j2,
                    j3=grasp_j3,
                    j4=form.grasp_j4,
                    j5=grasp_j5,
                )
                fit_result["j4_fit_rad"] = fitted_j4
        except Exception as exc:  # noqa: BLE001 - report, never crash the plan
            return self._task_invalid(
                "FIT_FAILED",
                f"the deep-family fit failed: {exc}",
                view_joints=view.joints,
                locate=report,
                current=current,
                simulated=simulated,
                form=form,
            )
        reasons = validate_grasp_target(
            target,
            fit_result,
            # A catalog j4 deliberately keeps the TCP off the fitted xy (the
            # pads, not the TCP, are the contact point), so only the object's
            # radius and finite joints gate that form. Gate the measured point,
            # not the +27 mm aim: the aim is a TCP offset, not the object.
            999.0 if form.grasp_j4 is not None else task.max_xy_error_mm,
            # A base-gated form is already inside its measured base window; the
            # cube's 0.220-0.255 m band does not apply (the catalog's cylinder
            # was measured at x ~0.214 m).
            None if base_gated else (task.min_reach_m, task.max_reach_m),
            radius_m=(
                float(np.linalg.norm(point[:2]))
                if form.grasp_j4 is not None and not base_gated
                else None
            ),
        )
        if reasons:
            approach = plan_approach(point, task.approach)
            if approach is not None:
                return self._task_invalid(
                    "APPROACH_REQUIRED",
                    "the target is outside the direct-grasp band ("
                    + "; ".join(reasons)
                    + "); pulse the chassis and re-locate",
                    view_joints=view.joints,
                    locate=report,
                    fit=fit_result,
                    current=current,
                    simulated=simulated,
                    approach=approach,
                    form=form,
                )
            return self._task_invalid(
                "FIT_REJECTED",
                "; ".join(reasons),
                view_joints=view.joints,
                locate=report,
                fit=fit_result,
                current=current,
                simulated=simulated,
                form=form,
            )
        try:
            gate = self._pose_gate_for()
        except VisualModelError as exc:
            return self._task_invalid(
                ErrorCode.FK_UNAVAILABLE.value,
                "URDF FK is unavailable: " + "; ".join(exc.issues),
                view_joints=view.joints,
                locate=report,
                fit=fit_result,
                current=current,
                simulated=simulated,
                form=form,
            )
        gripper = state.gripper
        start_gripper = (
            gripper.position_rad
            if gripper is not None and gripper.position_rad is not None
            else takeover.gripper.open_rad
        )
        locate_summary = {
            k: report.get(k)
            for k in ("cloud", "floor", "segmentation", "selected", "rejection")
            if k in report
        }
        locate_summary["success"] = bool(report.get("success"))
        locate_summary["previous"] = {
            name: {
                "rad": round(float(basis[name]), 6),
                "raw": raw_pulses.get(name),
            }
            for name in ARM_JOINTS
        }
        hand_eye_summary = {
            "method": "TSAI",
            "path": task.hand_eye_path,
            "frame": "link4 -> camera optical (depth_camera_link layout)",
        }
        plan = build_task_plan(
            takeover=takeover,
            mapping=self.mapping,
            limits=self.motion_limits,
            gate=gate,
            fit_result=fit_result,
            point=[float(value) for value in point],
            start_joints=basis,
            start_gripper_rad=float(start_gripper),
            source=source,
            locate_summary=locate_summary,
            hand_eye_summary=hand_eye_summary,
            simulated=simulated,
            form=form,
        )
        recovered = (report.get("selected") or {}).get("top_recovered")
        if isinstance(recovered, dict):
            plan["warnings"].append(
                {
                    "label": "locate",
                    "code": "TOP_CLIPPED_RECOVERED",
                    "message": (
                        "the min-range sphere clipped the near top edge; the "
                        "cube centre was rebuilt from the far edge with the "
                        f"measured {recovered.get('cube_size_m', 0.0) * 1000:.1f} mm cube size "
                        f"(centroid bias {recovered.get('centroid_bias_mm', 0.0):.1f} mm, "
                        f"clipped side {recovered.get('clipped_side')})"
                    ),
                }
            )
        plan["plan_id"] = f"task_{uuid.uuid4().hex[:16]}"
        plan["generated_at"] = utc_now_iso()
        plan["cloud"] = meta
        with self._lock:
            self._last_task_plan = {
                "plan": plan,
                "point": [float(value) for value in point],
                "target": [float(value) for value in target],
                "view": dict(view.joints),
                "source": source,
                "simulated": simulated,
                "form_id": form.form_id,
            }
        return plan

    def task_verify(
        self,
        plan_id: str | None = None,
        *,
        expect_view: bool = True,
    ) -> dict[str, Any]:
        """Check the cube box at the view after the lift.

        Returns ``held`` together with the segmentation report. A failed
        segmentation is reported, never silently converted to a held claim.
        """
        takeover, task = self._task_context()
        with self._lock:
            stored = self._last_task_plan
        if stored is None:
            raise ServiceError(
                "TASK_NOT_PLANNED",
                "no task plan exists; locate and plan first",
                status_code=409,
            )
        plan = stored["plan"]
        if plan_id and plan.get("plan_id") != plan_id:
            raise ServiceError(
                "TASK_PLAN_STALE",
                "the supplied plan_id is not the current plan",
                status_code=409,
            )
        state = self._state()
        simulated = bool(stored.get("simulated"))
        view = takeover.preset(task.view_preset)
        current = {
            joint.name: joint.position_rad
            for joint in state.joints
            if joint.position_rad is not None
        }
        if expect_view and view is not None:
            if set(current) == set(ARM_JOINTS):
                offsets = {
                    name: abs(
                        float(current[name]) - float(view.joints[name])
                    )
                    for name in ARM_JOINTS
                }
                if max(offsets.values()) > 0.12:
                    return {
                        "held": None,
                        "verified": False,
                        "code": "TASK_NOT_AT_VIEW",
                        "message": "the arm is not at the view pose; the "
                        "grip check needs the view",
                        "point": stored["point"],
                        "simulated": simulated,
                    }
        basis, _raw, basis_source = (
            self._task_pose_basis(state, view)
            if view is not None
            else (
                {name: float(value) for name, value in current.items()},
                {},
                "commanded",
            )
        )
        if simulated:
            return {
                "held": True,
                "verified": False,
                "simulated": True,
                "code": "SIMULATED_VERIFY",
                "message": "mock mode assumes the grip; no camera was read",
                "point": stored["point"],
                "clusters": 0,
                "report": {"mode": "simulated_verify"},
                "source": "simulated",
            }
        cloud = self.adapter.pointcloud()
        if cloud is None or cloud.get("points") is None:
            raise ServiceError(
                ErrorCode.POINTCLOUD_UNAVAILABLE.value,
                "the gateway has no registered point cloud for the grip check",
                status_code=503,
            )
        meta = cloud["meta"] if isinstance(cloud.get("meta"), dict) else {}
        age = meta.get("sample_age_ms")
        if isinstance(age, (int, float)) and age > 1500:
            raise ServiceError(
                ErrorCode.POINTCLOUD_STALE.value,
                f"the grip-check cloud is {age:.0f} ms old",
                status_code=409,
            )
        try:
            hand_eye = self._task_hand_eye(task)
            link4 = self._task_link4(basis)
            report = segment_floor_cube(
                cloud["points"],
                link4 @ hand_eye,
                task.locate,
                cloud_meta={
                    "frame_id": meta.get("frame_id"),
                    "topic": meta.get("topic"),
                    "sample_age_ms": age,
                    "layout": meta.get("layout"),
                },
            )
        except VisualModelError as exc:
            raise ServiceError(
                ErrorCode.FK_UNAVAILABLE.value,
                "URDF FK is unavailable: " + "; ".join(exc.issues),
                status_code=503,
            ) from exc
        held = not cube_box_present(
            report,
            stored["point"],
            task.verify_radius_m,
            task.verify_min_height_m,
        )
        clusters = int(report.get("segmentation", {}).get("clusters") or 0)
        warning = None
        if not report.get("success") and clusters == 0:
            warning = (
                "segmentation returned no clusters; 'held' is the phase-3 box "
                "rule, not a positive grip observation"
            )
        return {
            "held": held,
            "verified": True,
            "simulated": False,
            "point": stored["point"],
            "clusters": clusters,
            "report": report,
            "source": basis_source,
            "warning": warning,
        }

    # ---------------------------------------------------------------- mock ops
