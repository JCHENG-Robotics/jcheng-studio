"""G8 task plan: the validated floor-cube sequence, step by step.

The plan is built from a measured grasp point and the deep-family fit, then
each pose command is validated (limits, velocity, FK gate) **from the previous
command's target**. Every pose command carries an S-curve profile
(``domain.profile``): the dense waypoint list is built at execute time from the
current pose, so the arm moves fast and smooth instead of doing one long
constant-velocity point.

The plan groups the commands into a few operator-facing steps: one row per
logical phase (approach, grasp, close, lift, verify, release, return). The UI
and ``tools/studio/g8_task_run.py`` execute the commands in order through the
ordinary motion preview/execute endpoints, so the gateway's bus verification
still applies to every arm move. The plan is only a proposal: nothing here
dispatches motion.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from .mapping import MappingConfig
from .motion import EffectiveMotionLimits
from .models import MotionTarget
from .profile import ProfileParams, plan_move
from .takeover import TakeoverConfig, TaskFormConfig
from ..safety.pose_gate import PoseGate
from ..safety.validation import ValidationOutcome, validate_targets

ARM_JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5")

# An open command is skipped when the gripper is already this close to the
# measured open stop (about 14 raw). After a release the gripper is open, so a
# repeated run does not pay for a no-op gripper step.
OPEN_SKIP_RAD = 0.06


def _targets(
    joints: dict[str, float], duration: float
) -> list[dict[str, Any]]:
    return [
        {
            "joint": name,
            "target_rad": float(joints[name]),
            "duration_s": float(duration),
        }
        for name in ARM_JOINTS
    ]


def _normalized_map(normalized: list[dict[str, Any]]) -> dict[str, Any]:
    return {item["joint"]: item for item in normalized}


def build_task_plan(
    *,
    takeover: TakeoverConfig,
    mapping: MappingConfig,
    limits: EffectiveMotionLimits,
    gate: PoseGate,
    fit_result: dict[str, Any],
    point: list[float],
    start_joints: dict[str, float],
    start_gripper_rad: float,
    source: str,
    locate_summary: dict[str, Any] | None = None,
    hand_eye_summary: dict[str, Any] | None = None,
    simulated: bool = False,
    form: TaskFormConfig | None = None,
) -> dict[str, Any]:
    """Build and validate the full task plan; return the public plan.

    ``form`` is one measured object form from the 2026-11-23 catalog. It
    overrides the cube's grip closure, wrist depth and target correction;
    ``None`` keeps the historical 39.4 mm-cube plan exactly.
    """
    task = takeover.task
    if task is None:
        raise ValueError("the takeover contract has no task block")
    fast = task.fast
    if not fast.enabled:
        raise ValueError(
            "the fast task profile is disabled in configs/takeover.yaml "
            "(task.fast.enabled)"
        )
    view = takeover.preset(task.view_preset)
    if view is None:
        raise ValueError("the task view preset is missing")
    if form is not None and form.mode != "task":
        raise ValueError(
            f"task form {form.form_id!r} is mode {form.mode!r}; only a 'task' "
            "form can be planned"
        )
    grasp_j2 = form.grasp_j2 if form and form.grasp_j2 is not None else task.grasp_j2
    grasp_j3 = form.grasp_j3 if form and form.grasp_j3 is not None else task.grasp_j3
    grasp_j5 = form.grasp_j5 if form and form.grasp_j5 is not None else task.grasp_j5
    preclose_rad = (
        form.preclose_rad
        if form and form.preclose_rad is not None
        else task.preclose_rad
    )
    grip_rad = form.grip_rad if form and form.grip_rad is not None else task.grip_rad
    correction_mm = (
        form.correction_mm
        if form and form.correction_mm is not None
        else task.correction_mm
    )
    fit = {
        name: float(fit_result["joints"][index])
        for index, name in enumerate(ARM_JOINTS)
    }
    approach = {
        "joint1": fit["joint1"],
        "joint2": grasp_j2,
        "joint3": grasp_j3,
        "joint4": 0.0,
        "joint5": grasp_j5,
    }
    sign = 1.0 if fit["joint4"] >= approach["joint4"] else -1.0
    pre_grasp = dict(fit)
    pre_grasp["joint4"] = fit["joint4"] - sign * task.pre_grasp_offset_rad
    view_joints = dict(view.joints)
    fast_gripper = replace(
        takeover.gripper,
        speed_raw_per_s=fast.gripper_speed_raw_per_s,
        min_duration_s=fast.gripper_min_duration_s,
        max_duration_s=max(takeover.gripper.max_duration_s, 5.0),
    )

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []

    def fail(label: str, code: str, message: str, joint: str | None = None):
        errors.append(
            {
                "label": label,
                "code": code,
                "message": message,
                "joint": joint,
            }
        )

    def check_outcome(
        label: str,
        outcome: ValidationOutcome,
        check,
        start: dict[str, float],
    ) -> None:
        if not outcome.valid:
            for issue in outcome.errors:
                fail(label, issue.code, issue.message, issue.joint)
        if check is not None:
            if not check.ok:
                for violation in check.violations:
                    fail(label, violation.code, violation.message, violation.joint)
            if not check.start_compliant:
                warnings.append(
                    {
                        "label": label,
                        "code": "START_OUTSIDE_GATE",
                        "message": "the planned start is outside the FK gate; it "
                        "is exempt so the arm can be commanded back",
                    }
                )

    def profile_command(
        label: str,
        start: dict[str, float],
        target: dict[str, float],
        vmax: float,
    ) -> tuple[dict[str, Any], dict[str, float]]:
        params = ProfileParams(
            vmax_rad_s=float(vmax),
            amax_rad_s2=float(fast.amax_rad_s2),
            dt_s=float(fast.dt_s),
        )
        profile = plan_move(start, target, params)
        duration = float(profile.total_s)
        targets = _targets(target, duration)
        outcome = validate_targets(
            mapping,
            [
                MotionTarget(item["joint"], item["target_rad"], duration)
                for item in targets
            ],
            current_targets=start,
            max_step_rad=limits.max_step_rad,
            min_duration_s=0.0,
            max_duration_s=limits.max_duration_s,
            single_joint_only=False,
            scope="pose",
            max_velocity_rad_s=vmax,
            gripper=fast_gripper,
        )
        check = gate.check(start, target)
        check_outcome(label, outcome, check, start)
        normalized = _normalized_map(outcome.normalized)
        command = {
            "kind": "pose",
            "label": label,
            "joints": {name: round(value, 6) for name, value in target.items()},
            "from_joints": {
                name: round(value, 6) for name, value in start.items()
            },
            "targets": targets,
            "duration_s": duration,
            "profile": {
                **params.to_public_dict(),
                "segments": profile.segments,
                "total_s": round(duration, 6),
            },
            "target_raw": {
                name: normalized.get(name, {}).get("target_raw")
                for name in ARM_JOINTS
            },
            "pose_check": check.to_public_dict(),
        }
        return command, target

    def grip_command(
        label: str,
        start_rad: float,
        target_rad: float,
        duration: float,
    ) -> tuple[dict[str, Any], float]:
        targets = [
            {
                "joint": takeover.gripper.joint,
                "target_rad": float(target_rad),
                "duration_s": float(duration),
            }
        ]
        outcome = validate_targets(
            mapping,
            [
                MotionTarget(
                    takeover.gripper.joint, float(target_rad), float(duration)
                )
            ],
            current_targets={takeover.gripper.joint: float(start_rad)},
            max_step_rad=limits.max_step_rad,
            min_duration_s=0.0,
            max_duration_s=limits.max_duration_s,
            single_joint_only=False,
            scope="pose",
            max_velocity_rad_s=None,
            gripper=fast_gripper,
        )
        check_outcome(label, outcome, None, {})
        normalized = _normalized_map(outcome.normalized)
        command = {
            "kind": "grip",
            "label": label,
            "target_rad": round(float(target_rad), 6),
            "from_rad": round(float(start_rad), 6),
            "targets": targets,
            "duration_s": float(duration),
            "target_raw": normalized.get(takeover.gripper.joint, {}).get(
                "target_raw"
            ),
            "fast": True,
        }
        return command, float(target_rad)

    def add_step(
        label: str, kind: str, commands: list[dict[str, Any]], **extra: Any
    ) -> None:
        steps.append(
            {
                "kind": kind,
                "label": label,
                "commands": commands,
                "duration_s": round(
                    sum(float(item["duration_s"]) for item in commands), 6
                ),
                "status": "pending",
                **extra,
            }
        )

    current = {name: float(start_joints.get(name, 0.0)) for name in ARM_JOINTS}
    gripper_now = float(start_gripper_rad)

    if abs(gripper_now - takeover.gripper.open_rad) > OPEN_SKIP_RAD:
        command, gripper_now = grip_command(
            "open_gripper",
            gripper_now,
            takeover.gripper.open_rad,
            fast.gripper_open_duration_s,
        )
        add_step("open_gripper", "grip", [command])

    command, current = profile_command(
        "approach", current, approach, fast.vmax_rad_s
    )
    add_step("approach", "pose", [command])

    descend, current = profile_command(
        "descend", current, pre_grasp, fast.descent_vmax_rad_s
    )
    final, current = profile_command(
        "grasp", current, fit, fast.final_vmax_rad_s
    )
    add_step("grasp", "pose", [descend, final])

    close_pre, gripper_now = grip_command(
        "close_pre",
        gripper_now,
        preclose_rad,
        fast.gripper_preclose_duration_s,
    )
    close_grip, gripper_now = grip_command(
        "close_grip",
        gripper_now,
        grip_rad,
        fast.gripper_grip_duration_s,
    )
    add_step("close", "grip", [close_pre, close_grip])

    # The lift and the return to the view are one fast move: the measured
    # 2026-11-20 sequence goes from the grasp pose straight to the verify pose
    # at 4.0 rad/s instead of through a separate 150 mm lift preset.
    command, current = profile_command(
        "lift", current, view_joints, fast.vmax_rad_s
    )
    add_step("lift", "pose", [command])

    add_step(
        "grip_verify",
        "verify",
        [],
        detail="fetch a cloud at the view and check the cube box",
    )

    release_descend, current = profile_command(
        "release_descend",
        current,
        pre_grasp,
        fast.release_descent_vmax_rad_s,
    )
    release_final, current = profile_command(
        "release_final", current, fit, fast.release_final_vmax_rad_s
    )
    # A catalog form closes to −0.05 rad (raw ~688), so a 0.45 s open would
    # interpolate at >900 raw/s and be rejected. Keep the measured 0.45 s for
    # the cube and stretch only when the close target is deeper.
    open_delta_raw = abs(
        takeover.gripper.open_rad - grip_rad
    ) / takeover.gripper.radians_per_tick
    release_open_duration = max(
        fast.gripper_release_open_duration_s,
        math.ceil(
            open_delta_raw / fast.gripper_speed_raw_per_s * 100.0
        )
        / 100.0,
    )
    release_open, gripper_now = grip_command(
        "release_open",
        gripper_now,
        takeover.gripper.open_rad,
        release_open_duration,
    )
    add_step(
        "release", "mixed", [release_descend, release_final, release_open]
    )

    command, current = profile_command(
        "return_view", current, view_joints, fast.vmax_rad_s
    )
    add_step("return_view", "pose", [command])

    if simulated:
        warnings.append(
            {
                "label": "task",
                "code": "SIMULATED_LOCATE",
                "message": "mock mode uses a fixed object point (the form base "
                "when set); no camera was read",
            }
        )
    if source == "commanded":
        warnings.append(
            {
                "label": "task",
                "code": "START_ECHO",
                "message": "the locate used the command cache (up to ~8 pulses "
                "from the actual); each move is bus-verified",
            }
        )
    if form is not None:
        if form.grasp_j4 is not None:
            fitted_j4 = fit_result.get("j4_fit_rad")
            fitted_text = (
                f"（拟合值 {float(fitted_j4):.3f}）"
                if isinstance(fitted_j4, (int, float))
                else ""
            )
            warnings.append(
                {
                    "label": "task",
                    "code": "FORM_J4_MEASURED",
                    "message": (
                        f"{form.label}：joint4 固定为现场实测 "
                        f"{fit['joint4']:.3f} rad{fitted_text}。TCP 的 xy "
                        "残差是设计使然：接触点是夹爪垫，不是 TCP。"
                    ),
                }
            )
        if form.correction_mm is not None:
            warnings.append(
                {
                    "label": "task",
                    "code": "FORM_AIM_OVERRIDE",
                    "message": (
                        f"{form.label}：瞄准修正使用实测值 "
                        f"[{', '.join(f'{value:.1f}' for value in form.correction_mm)}] mm。"
                    ),
                }
            )
        if form.grasp_j4 is not None:
            if form.base_point_m is not None:
                base_x, base_y = form.base_point_m
                base_message = (
                    f"{form.label}：j4 是目录基准位姿的实测值；基准窗口 "
                    f"({base_x:.3f}, {base_y:.3f}) ± "
                    f"{form.base_tolerance_m * 1000.0:.0f} mm。物体不在窗口内时，"
                    "运行会先底盘脉冲到该基准位再抓。"
                )
            else:
                base_message = (
                    f"{form.label}：j4 是目录基准位姿的实测值，G8.1 只把物体"
                    "送到 0.230 m；若记录基准位不同，先手动把物体移到目录位置。"
                )
            warnings.append(
                {
                    "label": "task",
                    "code": "FORM_BASE_POSE",
                    "message": base_message,
                }
            )
        if form.notes:
            warnings.append(
                {
                    "label": "task",
                    "code": "FORM_NOTES",
                    "message": form.notes,
                }
            )
    fast_public = fast.to_public_dict()
    fast_public["mode"] = "s_curve"
    return {
        "schema_version": 1,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "simulated": simulated,
        "form": form.to_public_dict() if form is not None else None,
        "form_id": form.form_id if form is not None else None,
        "point": [round(float(value), 6) for value in point],
        "target": [
            round(float(value) + float(correction_mm[index]) / 1000.0, 6)
            for index, value in enumerate(point)
        ],
        "correction_mm": list(correction_mm),
        "preclose_rad": round(float(preclose_rad), 6),
        "grip_rad": round(float(grip_rad), 6),
        "grasp_j2": round(float(grasp_j2), 6),
        "grasp_j3": round(float(grasp_j3), 6),
        "grasp_j4": (
            round(float(form.grasp_j4), 6)
            if form is not None and form.grasp_j4 is not None
            else None
        ),
        "grasp_j5": round(float(grasp_j5), 6),
        "fit": fit_result,
        "grasp_joints": {name: round(value, 6) for name, value in fit.items()},
        "approach_joints": approach,
        "pre_grasp_joints": pre_grasp,
        "view_joints": view_joints,
        "lift_joints": view_joints,
        "view_preset": task.view_preset,
        "lift_preset": task.view_preset,
        "start_joints": {
            name: round(value, 6) for name, value in start_joints.items()
        },
        "start_gripper_rad": round(float(start_gripper_rad), 6),
        "source": source,
        "locate": locate_summary or {},
        "hand_eye": hand_eye_summary or {},
        "fast": fast_public,
        "steps": steps,
    }
