"""G7 pose-takeover contract.

The G6 write path was deliberately tiny: one arm joint, 23 pulses, one second
minimum. G7 keeps the same "preview -> confirm -> execute" flow but lifts the
scope to one **whole five-joint pose** plus one **gripper** command. This module
holds the limits that only make sense for a pose command, and a small library
of poses that have actually run on the hardware.

This file has no ROS, FastAPI or adapter imports so the safety core, the
service and the tests can share it.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .locate import LocateParams

# Enabling a multi-joint write surface needs an action in the launch command,
# not a checkbox in the UI. The phrase is intentionally different from the G6
# single-joint acknowledgement.
TAKEOVER_ACK = "I_AM_AT_THE_ROBOT_WITH_A_CLEAR_WORKSPACE"

ARM_JOINTS: tuple[str, ...] = ("joint1", "joint2", "joint3", "joint4", "joint5")
GRIPPER_JOINT = "r_joint"
GRIPPER_SERVO_ID = 1
GRIPPER_INIT_RAW = 700


class TakeoverConfigError(ValueError):
    """The takeover contract file is missing or malformed."""


@dataclass(frozen=True)
class GripperLimit:
    """One gripper servo, measured with the vendor init-700 convention."""

    joint: str
    servo_id: int
    init_raw: int
    open_raw: int
    close_raw: int
    measured_min_raw: int
    measured_max_raw: int
    speed_raw_per_s: float
    min_duration_s: float
    max_duration_s: float
    completion: str = "timed"

    def raw_to_rad(self, raw: float) -> float:
        return (float(raw) - self.init_raw) * self.radians_per_tick

    def rad_to_raw(self, rad: float) -> float:
        return self.init_raw + float(rad) / self.radians_per_tick

    def pulse_to_rad(self, pulse: int) -> float:
        return self.raw_to_rad(pulse)

    def rad_to_pulse(self, rad: float) -> float:
        return self.rad_to_raw(rad)

    @property
    def radians_per_tick(self) -> float:
        # The vendor uses one uniform 240 deg / 1000 tick assumption for all
        # six bus servos; the arm mapping carries the same value.
        return 240.0 / 360.0 * (2.0 * math.pi) / 1000.0

    @property
    def open_rad(self) -> float:
        return self.raw_to_rad(self.open_raw)

    @property
    def close_rad(self) -> float:
        return self.raw_to_rad(self.close_raw)

    @property
    def min_rad(self) -> float:
        return min(self.open_rad, self.close_rad)

    @property
    def max_rad(self) -> float:
        return max(self.open_rad, self.close_rad)

    @property
    def measured_min_rad(self) -> float:
        return self.raw_to_rad(self.measured_min_raw)

    @property
    def measured_max_rad(self) -> float:
        return self.raw_to_rad(self.measured_max_raw)

    def suggested_duration_s(self, delta_raw: float) -> float:
        scaled = abs(float(delta_raw)) / self.speed_raw_per_s
        return min(
            self.max_duration_s,
            max(self.min_duration_s, math.ceil(scaled * 10.0) / 10.0),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "joint": self.joint,
            "servo_id": self.servo_id,
            "init_raw": self.init_raw,
            "open_raw": self.open_raw,
            "close_raw": self.close_raw,
            "measured_min_raw": self.measured_min_raw,
            "measured_max_raw": self.measured_max_raw,
            "open_rad": round(self.open_rad, 6),
            "close_rad": round(self.close_rad, 6),
            "min_rad": round(self.min_rad, 6),
            "max_rad": round(self.max_rad, 6),
            "speed_raw_per_s": self.speed_raw_per_s,
            "min_duration_s": self.min_duration_s,
            "max_duration_s": self.max_duration_s,
            "completion": self.completion,
        }


@dataclass(frozen=True)
class WorkspaceBox:
    frame: str
    enabled: bool
    lower: tuple[float, float, float]
    upper: tuple[float, float, float]

    def contains(self, point: tuple[float, float, float]) -> bool:
        return all(
            self.lower[index] <= point[index] <= self.upper[index]
            for index in range(3)
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "enabled": self.enabled,
            "lower": list(self.lower),
            "upper": list(self.upper),
        }


@dataclass(frozen=True)
class TablePlane:
    frame: str
    enabled: bool
    normal: tuple[float, float, float]
    offset: float
    clearance_m: float

    def height(self, point: tuple[float, float, float]) -> float:
        """Signed distance from the plane (positive above it)."""
        return (
            self.normal[0] * point[0]
            + self.normal[1] * point[1]
            + self.normal[2] * point[2]
            + self.offset
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "enabled": self.enabled,
            "normal": list(self.normal),
            "offset": self.offset,
            "clearance_m": self.clearance_m,
        }


@dataclass(frozen=True)
class ChassisGuard:
    """Measured rule: do not yaw a low arm across the chassis."""

    enabled: bool
    low_tcp_z_m: float
    max_low_yaw_rad: float

    def violation(
        self,
        tcp: tuple[float, float, float],
        joint1_rad: float,
    ) -> str | None:
        if not self.enabled:
            return None
        if tcp[2] >= self.low_tcp_z_m:
            return None
        if abs(joint1_rad) <= self.max_low_yaw_rad:
            return None
        return (
            f"tcp_z={tcp[2]:.3f} m is below {self.low_tcp_z_m:.3f} m with "
            f"joint1={joint1_rad:.3f} rad (limit ±{self.max_low_yaw_rad:.3f})"
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "low_tcp_z_m": self.low_tcp_z_m,
            "max_low_yaw_rad": self.max_low_yaw_rad,
        }


@dataclass(frozen=True)
class PosePreset:
    preset_id: str
    label: str
    label_en: str
    joints: dict[str, float]
    verified_on_hardware: bool
    source: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.preset_id,
            "label": self.label,
            "label_en": self.label_en,
            "joints": {name: round(value, 6) for name, value in self.joints.items()},
            "verified_on_hardware": self.verified_on_hardware,
            "source": self.source,
        }


@dataclass(frozen=True)
class FastProfile:
    """The G8 fast task path: one S-curve per pose, fast gripper timings.

    The values are the hardware-verified throughput-v2 envelope from
    2026-11-20 (``docs/phase2/FAST_MOTION.md``): 4.0 rad/s / 20 rad/s^2,
    0.6/0.35 rad/s descents, 1.4/0.8 rad/s release descents and the
    0.35/0.5/0.45 s gripper steps. A slow single-point move is not just
    slower, it shakes: the vendor sends one bus command per point.
    """

    enabled: bool = False
    vmax_rad_s: float = 4.0
    amax_rad_s2: float = 20.0
    dt_s: float = 0.05
    descent_vmax_rad_s: float = 0.6
    final_vmax_rad_s: float = 0.35
    release_descent_vmax_rad_s: float = 1.4
    release_final_vmax_rad_s: float = 0.8
    gripper_open_duration_s: float = 0.8
    gripper_preclose_duration_s: float = 0.35
    gripper_grip_duration_s: float = 0.5
    gripper_release_open_duration_s: float = 0.45
    gripper_speed_raw_per_s: float = 900.0
    gripper_min_duration_s: float = 0.25
    gripper_settle_s: float = 0.6
    verify_settle_s: float = 0.35
    max_total_duration_s: float = 6.0
    max_points: int = 400

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "vmax_rad_s": self.vmax_rad_s,
            "amax_rad_s2": self.amax_rad_s2,
            "dt_s": self.dt_s,
            "descent_vmax_rad_s": self.descent_vmax_rad_s,
            "final_vmax_rad_s": self.final_vmax_rad_s,
            "release_descent_vmax_rad_s": self.release_descent_vmax_rad_s,
            "release_final_vmax_rad_s": self.release_final_vmax_rad_s,
            "gripper_open_duration_s": self.gripper_open_duration_s,
            "gripper_preclose_duration_s": self.gripper_preclose_duration_s,
            "gripper_grip_duration_s": self.gripper_grip_duration_s,
            "gripper_release_open_duration_s": self.gripper_release_open_duration_s,
            "gripper_speed_raw_per_s": self.gripper_speed_raw_per_s,
            "gripper_min_duration_s": self.gripper_min_duration_s,
            "gripper_settle_s": self.gripper_settle_s,
            "verify_settle_s": self.verify_settle_s,
            "max_total_duration_s": self.max_total_duration_s,
            "max_points": self.max_points,
        }


@dataclass(frozen=True)
class ApproachConfig:
    """G8.1 chassis approach: open-loop pulses closed by a re-measurement."""

    enabled: bool
    target_point_m: tuple[float, float] = (0.230, 0.0)
    tolerance_m: float = 0.008
    duty: float = 10.0
    speed_mm_s_per_duty: float = 4.9
    min_duration_s: float = 0.25
    max_duration_s: float = 2.0
    max_pulses: int = 3
    settle_s: float = 0.5

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "target_point_m": list(self.target_point_m),
            "tolerance_m": self.tolerance_m,
            "duty": self.duty,
            "speed_mm_s_per_duty": self.speed_mm_s_per_duty,
            "min_duration_s": self.min_duration_s,
            "max_duration_s": self.max_duration_s,
            "max_pulses": self.max_pulses,
            "settle_s": self.settle_s,
        }


TASK_FORM_MODES = ("task", "pose", "refused")


@dataclass(frozen=True)
class TaskFormConfig:
    """One measured object form from the 2026-11-23 grasp catalog.

    - ``task``    — the depth locate + plan can execute this form; the fields
      below override the cube defaults.
    - ``pose``    — physically grasped on hardware, but the depth pipeline
      cannot locate it (the VLT carton has no depth return); the recorded pose
      is for pose takeover, not for this plan.
    - ``refused`` — measured to fail (below the pads' vertical reach, or
      squeezed out of the rigid pads); the reason is shown before anything is
      planned.

    ``grasp_j4`` is the **measured** wrist depth that worked on hardware, not
    a planner output. ``base_point_m`` is the measured object position at that
    working pose; when the object is outside ``base_tolerance_m`` of it, the
    G8.1 loop pulses the base to this point instead of the cube's
    ``(0.230, 0.0)``. joint1 is still solved from the located target azimuth.
    """

    form_id: str
    label: str
    label_en: str
    mode: str = "task"
    reason: str = ""
    size_text: str = ""
    top_height_mm: float | None = None
    close_register: int | None = None
    grasp_j2: float | None = None
    grasp_j3: float | None = None
    grasp_j4: float | None = None
    grasp_j5: float | None = None
    preclose_rad: float | None = None
    grip_rad: float | None = None
    correction_mm: tuple[float, float, float] | None = None
    base_point_m: tuple[float, float] | None = None
    base_tolerance_m: float = 0.012
    recorded_pose: dict[str, float] | None = None
    notes: str = ""
    catalog: str = "docs/phase2/OBJECT_GRASP_CATALOG.md"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "form_id": self.form_id,
            "label": self.label,
            "label_en": self.label_en,
            "mode": self.mode,
            "reason": self.reason,
            "size_text": self.size_text,
            "top_height_mm": self.top_height_mm,
            "close_register": self.close_register,
            "grasp_j2": self.grasp_j2,
            "grasp_j3": self.grasp_j3,
            "grasp_j4": self.grasp_j4,
            "grasp_j5": self.grasp_j5,
            "preclose_rad": self.preclose_rad,
            "grip_rad": self.grip_rad,
            "correction_mm": (
                list(self.correction_mm) if self.correction_mm else None
            ),
            "base_point_m": (
                list(self.base_point_m) if self.base_point_m else None
            ),
            "base_tolerance_m": self.base_tolerance_m,
            "recorded_pose": (
                dict(self.recorded_pose) if self.recorded_pose else None
            ),
            "notes": self.notes,
            "catalog": self.catalog,
        }


@dataclass(frozen=True)
class TaskConfig:
    """G8 task contract: locate + the validated deep grasp family."""

    hand_eye_path: str
    correction_mm: tuple[float, float, float]
    view_preset: str
    lift_preset: str
    grasp_j2: float
    grasp_j3: float
    grasp_j5: float
    pre_grasp_offset_rad: float
    preclose_rad: float
    grip_rad: float
    grip_preclose_duration_s: float
    grip_duration_s: float
    release_open_duration_s: float
    verify_radius_m: float
    verify_min_height_m: float
    min_reach_m: float
    max_reach_m: float
    max_xy_error_mm: float
    locate: LocateParams
    approach: ApproachConfig | None = None
    fast: FastProfile = FastProfile()
    forms: tuple[TaskFormConfig, ...] = ()
    default_form: str = ""

    def form(self, form_id: str) -> TaskFormConfig | None:
        for item in self.forms:
            if item.form_id == form_id:
                return item
        return None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "hand_eye_path": self.hand_eye_path,
            "correction_mm": list(self.correction_mm),
            "view_preset": self.view_preset,
            "lift_preset": self.lift_preset,
            "grasp_j2": self.grasp_j2,
            "grasp_j3": self.grasp_j3,
            "grasp_j5": self.grasp_j5,
            "pre_grasp_offset_rad": self.pre_grasp_offset_rad,
            "preclose_rad": self.preclose_rad,
            "grip_rad": self.grip_rad,
            "grip_preclose_duration_s": self.grip_preclose_duration_s,
            "grip_duration_s": self.grip_duration_s,
            "release_open_duration_s": self.release_open_duration_s,
            "verify_radius_m": self.verify_radius_m,
            "verify_min_height_m": self.verify_min_height_m,
            "min_reach_m": self.min_reach_m,
            "max_reach_m": self.max_reach_m,
            "max_xy_error_mm": self.max_xy_error_mm,
            "locate": (
                self.locate.to_public_dict()
                if hasattr(self.locate, "to_public_dict")
                else self.locate
            ),
            "approach": (
                self.approach.to_public_dict() if self.approach else None
            ),
            "fast": self.fast.to_public_dict(),
            "forms": [item.to_public_dict() for item in self.forms],
            "default_form": self.default_form,
        }


@dataclass(frozen=True)
class TakeoverConfig:
    schema_version: int
    scope: str
    max_velocity_rad_s: float
    min_duration_s: float
    max_duration_s: float
    path_samples: int
    completion_tolerance_pulses: int
    completion_settle_samples: int
    timeout_margin_s: float
    gripper: GripperLimit
    workspace: WorkspaceBox
    table_plane: TablePlane
    chassis_guard: ChassisGuard
    presets: tuple[PosePreset, ...]
    task: TaskConfig | None = None
    source_path: str = ""
    config_hash: str = ""

    def preset(self, preset_id: str) -> PosePreset | None:
        for item in self.presets:
            if item.preset_id == preset_id:
                return item
        return None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope,
            "max_velocity_rad_s": self.max_velocity_rad_s,
            "min_duration_s": self.min_duration_s,
            "max_duration_s": self.max_duration_s,
            "path_samples": self.path_samples,
            "completion_tolerance_pulses": self.completion_tolerance_pulses,
            "completion_settle_samples": self.completion_settle_samples,
            "timeout_margin_s": self.timeout_margin_s,
            "gripper": self.gripper.to_public_dict(),
            "workspace": self.workspace.to_public_dict(),
            "table_plane": self.table_plane.to_public_dict(),
            "chassis_guard": self.chassis_guard.to_public_dict(),
            "presets": [item.to_public_dict() for item in self.presets],
            "task": (
                self.task.to_public_dict() if self.task is not None else None
            ),
            "config_hash": self.config_hash,
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TakeoverConfigError(message)


def _triple(value: Any, name: str) -> tuple[float, float, float]:
    _require(
        isinstance(value, (list, tuple)) and len(value) == 3,
        f"{name} must have 3 values",
    )
    values = tuple(float(item) for item in value)
    _require(all(math.isfinite(item) for item in values), f"{name} must be finite")
    return values  # type: ignore[return-value]


def load_takeover_config(path: str | Path) -> TakeoverConfig:
    path = Path(path)
    if not path.is_file():
        raise TakeoverConfigError(f"takeover config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - malformed user file
        raise TakeoverConfigError(f"invalid YAML in {path}: {exc}") from exc
    _require(isinstance(raw, dict), "takeover config root must be a mapping")

    schema_version = int(raw.get("schema_version", 1))
    _require(schema_version == 1, f"unsupported takeover schema_version: {schema_version}")
    scope = str(raw.get("scope", "pose")).strip().lower()
    _require(scope == "pose", f"takeover scope must be 'pose', got {scope!r}")

    velocity = float(raw.get("max_velocity_rad_s", 0.5))
    _require(0.05 <= velocity <= 1.2, "max_velocity_rad_s must be in [0.05, 1.2]")
    min_duration = float(raw.get("min_duration_s", 1.0))
    max_duration = float(raw.get("max_duration_s", 10.0))
    _require(0.5 <= min_duration <= max_duration <= 30.0, "invalid duration range")
    path_samples = int(raw.get("path_samples", 21))
    _require(2 <= path_samples <= 201, "path_samples must be 2..201")
    tolerance = int(raw.get("completion_tolerance_pulses", 3))
    settle = int(raw.get("completion_settle_samples", 3))
    _require(0 <= tolerance <= 10, "completion_tolerance_pulses must be 0..10")
    _require(1 <= settle <= 10, "completion_settle_samples must be 1..10")
    margin = float(raw.get("timeout_margin_s", 3.0))
    _require(0.0 <= margin <= 10.0, "timeout_margin_s must be 0..10")

    gripper_raw = raw.get("gripper")
    _require(isinstance(gripper_raw, dict), "gripper must be a mapping")
    _require(
        str(gripper_raw.get("joint", GRIPPER_JOINT)) == GRIPPER_JOINT,
        f"gripper.joint must be {GRIPPER_JOINT}",
    )
    _require(
        int(gripper_raw.get("servo_id", GRIPPER_SERVO_ID)) == GRIPPER_SERVO_ID,
        f"gripper.servo_id must be {GRIPPER_SERVO_ID}",
    )
    gripper = GripperLimit(
        joint=GRIPPER_JOINT,
        servo_id=GRIPPER_SERVO_ID,
        init_raw=int(gripper_raw.get("init_raw", GRIPPER_INIT_RAW)),
        open_raw=int(gripper_raw["open_raw"]),
        close_raw=int(gripper_raw["close_raw"]),
        measured_min_raw=int(gripper_raw["measured_min_raw"]),
        measured_max_raw=int(gripper_raw["measured_max_raw"]),
        speed_raw_per_s=float(gripper_raw.get("speed_raw_per_s", 120.0)),
        min_duration_s=float(gripper_raw.get("min_duration_s", 1.0)),
        max_duration_s=float(gripper_raw.get("max_duration_s", 6.0)),
        completion=str(gripper_raw.get("completion", "timed")),
    )
    _require(gripper.init_raw == GRIPPER_INIT_RAW, "gripper.init_raw must be 700")
    _require(
        0 <= gripper.measured_min_raw < gripper.open_raw < gripper.close_raw,
        "gripper open must be above the measured open stop and below close",
    )
    _require(gripper.close_raw <= 1000, "gripper close must be <= 1000")
    _require(
        gripper.measured_max_raw <= gripper.close_raw,
        "measured gripper close stop must be inside the command",
    )
    _require(gripper.speed_raw_per_s > 0, "gripper speed must be positive")
    _require(
        0.5 <= gripper.min_duration_s <= gripper.max_duration_s <= 30.0,
        "invalid gripper duration range",
    )
    _require(
        gripper.completion in {"timed", "position"},
        "gripper completion must be 'timed' or 'position'",
    )

    workspace_raw = raw.get("workspace")
    _require(isinstance(workspace_raw, dict), "workspace must be a mapping")
    lower = _triple(workspace_raw.get("lower"), "workspace.lower")
    upper = _triple(workspace_raw.get("upper"), "workspace.upper")
    _require(
        all(lower[i] < upper[i] for i in range(3)),
        "workspace lower must be below upper",
    )
    workspace = WorkspaceBox(
        frame=str(workspace_raw.get("frame", "base_link")),
        enabled=bool(workspace_raw.get("enabled", True)),
        lower=lower,
        upper=upper,
    )

    table_raw = raw.get("table_plane")
    _require(isinstance(table_raw, dict), "table_plane must be a mapping")
    normal = _triple(table_raw.get("normal"), "table_plane.normal")
    norm = math.sqrt(sum(value * value for value in normal))
    _require(norm > 0, "table_plane.normal must be non-zero")
    _require(normal[2] > 0, "table_plane.normal must point up (+z)")
    clearance = float(table_raw.get("clearance_m", 0.01))
    _require(clearance >= 0, "table_plane.clearance_m must be non-negative")
    table = TablePlane(
        frame=str(table_raw.get("frame", "base_link")),
        enabled=bool(table_raw.get("enabled", True)),
        normal=normal,
        offset=float(table_raw.get("offset", 0.0)),
        clearance_m=clearance,
    )
    _require(
        workspace.frame == table.frame,
        "workspace and table_plane must use the same frame",
    )

    chassis_raw = raw.get("chassis_guard") or {}
    _require(isinstance(chassis_raw, dict), "chassis_guard must be a mapping")
    chassis = ChassisGuard(
        enabled=bool(chassis_raw.get("enabled", True)),
        low_tcp_z_m=float(chassis_raw.get("low_tcp_z_m", 0.09)),
        max_low_yaw_rad=float(chassis_raw.get("max_low_yaw_rad", 0.20)),
    )
    _require(0.0 <= chassis.max_low_yaw_rad <= 0.5, "chassis yaw limit must be 0..0.5")

    presets_raw = raw.get("presets") or []
    _require(isinstance(presets_raw, list), "presets must be a list")
    presets: list[PosePreset] = []
    seen: set[str] = set()
    for item in presets_raw:
        _require(isinstance(item, dict), "each preset must be a mapping")
        preset_id = str(item.get("id", "")).strip()
        _require(bool(preset_id), "preset id is required")
        _require(preset_id not in seen, f"duplicate preset id: {preset_id}")
        seen.add(preset_id)
        joints_raw = item.get("joints")
        _require(isinstance(joints_raw, dict), f"{preset_id}: joints must be a mapping")
        _require(
            set(joints_raw) == set(ARM_JOINTS),
            f"{preset_id}: joints must be exactly {', '.join(ARM_JOINTS)}",
        )
        joints = {name: float(joints_raw[name]) for name in ARM_JOINTS}
        _require(
            all(math.isfinite(value) for value in joints.values()),
            f"{preset_id}: joint values must be finite",
        )
        presets.append(
            PosePreset(
                preset_id=preset_id,
                label=str(item.get("label", preset_id)),
                label_en=str(item.get("label_en", preset_id)),
                joints=joints,
                verified_on_hardware=bool(item.get("verified_on_hardware", False)),
                source=str(item.get("source", "")),
            )
        )

    # ------------------------------------------------------------- G8 task
    task_raw = raw.get("task")
    task: TaskConfig | None = None
    if task_raw is not None:
        _require(isinstance(task_raw, dict), "task must be a mapping")
        hand_eye = str(task_raw.get("hand_eye", "")).strip()
        _require(bool(hand_eye), "task.hand_eye is required")
        hand_eye_path = (path.parent / hand_eye).resolve()
        _require(
            hand_eye_path.is_file(),
            f"task.hand_eye not found: {hand_eye_path}",
        )
        correction = _triple(task_raw.get("correction_mm"), "task.correction_mm")
        view_preset = str(task_raw.get("view_preset", "view_table"))
        lift_preset = str(task_raw.get("lift_preset", "lift_150"))
        preset_ids = {item.preset_id for item in presets}
        _require(
            view_preset in preset_ids,
            f"task.view_preset {view_preset!r} is not a preset",
        )
        _require(
            lift_preset in preset_ids,
            f"task.lift_preset {lift_preset!r} is not a preset",
        )
        locate_raw = task_raw.get("locate") or {}
        _require(isinstance(locate_raw, dict), "task.locate must be a mapping")
        x_range_raw = locate_raw.get("x_range_m", [0.15, 0.32])
        _require(
            isinstance(x_range_raw, (list, tuple)) and len(x_range_raw) == 2,
            "task.locate.x_range_m must have 2 values",
        )
        x_range = (float(x_range_raw[0]), float(x_range_raw[1]))
        _require(x_range[0] < x_range[1], "task.locate.x_range_m must ascend")
        locate = LocateParams(
            min_range_m=float(locate_raw.get("min_range_m", 0.15)),
            max_range_m=float(locate_raw.get("max_range_m", 0.60)),
            floor_threshold_m=float(locate_raw.get("floor_threshold_m", 0.004)),
            floor_margin_m=float(locate_raw.get("floor_margin_m", 0.006)),
            voxel_m=float(locate_raw.get("voxel_m", 0.005)),
            min_cluster_points=int(locate_raw.get("min_cluster_points", 30)),
            slab_m=float(locate_raw.get("slab_m", 0.005)),
            min_top_extent_m=float(locate_raw.get("min_top_extent_m", 0.030)),
            ransac_iterations=int(locate_raw.get("ransac_iterations", 800)),
            ransac_points=int(locate_raw.get("ransac_points", 40000)),
            x_range_m=x_range,
            y_range_m=float(locate_raw.get("y_range_m", 0.15)),
            max_xy_m=float(locate_raw.get("max_xy_m", 0.09)),
            min_height_m=float(locate_raw.get("min_height_m", 0.020)),
            max_height_m=float(locate_raw.get("max_height_m", 0.070)),
            seed=int(locate_raw.get("seed", 0)),
            cube_size_m=float(locate_raw.get("cube_size_m", 0.0)),
            recovery_min_visible_m=float(
                locate_raw.get("recovery_min_visible_m", 0.012)
            ),
            recovery_intact_extent_m=float(
                locate_raw.get("recovery_intact_extent_m", 0.030)
            ),
            recovery_edge_tol_m=float(
                locate_raw.get("recovery_edge_tol_m", 0.002)
            ),
            recovery_range_band_m=float(
                locate_raw.get("recovery_range_band_m", 0.0025)
            ),
            recovery_range_std_m=float(
                locate_raw.get("recovery_range_std_m", 0.0015)
            ),
            recovery_radial_min=float(
                locate_raw.get("recovery_radial_min", 0.85)
            ),
        )
        approach_raw = task_raw.get("approach") or {}
        _require(isinstance(approach_raw, dict), "task.approach must be a mapping")
        approach = None
        if approach_raw:
            approach_enabled = bool(approach_raw.get("enabled", False))
            target_raw = approach_raw.get("target_point_m", [0.230, 0.0])
            _require(
                isinstance(target_raw, (list, tuple)) and len(target_raw) == 2,
                "task.approach.target_point_m must have 2 values",
            )
            target_point = (float(target_raw[0]), float(target_raw[1]))
            _require(
                target_point[0] > 0.0,
                "task.approach.target_point_m x must be positive",
            )
            duty = float(approach_raw.get("duty", 10.0))
            _require(0.0 < duty <= 20.0, "task.approach.duty must be in (0, 20]")
            approach_min_duration = float(
                approach_raw.get("min_duration_s", 0.25)
            )
            approach_max_duration = float(
                approach_raw.get("max_duration_s", 2.0)
            )
            _require(
                0.0 < approach_min_duration < approach_max_duration <= 3.0,
                "task.approach durations must satisfy 0 < min < max <= 3.0 s",
            )
            max_pulses = int(approach_raw.get("max_pulses", 3))
            _require(max_pulses >= 1, "task.approach.max_pulses must be >= 1")
            approach = ApproachConfig(
                enabled=approach_enabled,
                target_point_m=target_point,
                tolerance_m=float(approach_raw.get("tolerance_m", 0.008)),
                duty=duty,
                speed_mm_s_per_duty=float(
                    approach_raw.get("speed_mm_s_per_duty", 4.9)
                ),
                min_duration_s=approach_min_duration,
                max_duration_s=approach_max_duration,
                max_pulses=max_pulses,
                settle_s=float(approach_raw.get("settle_s", 0.5)),
            )
        fast_raw = task_raw.get("fast") or {}
        _require(isinstance(fast_raw, dict), "task.fast must be a mapping")
        fast = FastProfile()
        if fast_raw:
            fast = FastProfile(
                enabled=bool(fast_raw.get("enabled", False)),
                vmax_rad_s=float(fast_raw.get("vmax_rad_s", 4.0)),
                amax_rad_s2=float(fast_raw.get("amax_rad_s2", 20.0)),
                dt_s=float(fast_raw.get("dt_s", 0.05)),
                descent_vmax_rad_s=float(
                    fast_raw.get("descent_vmax_rad_s", 0.6)
                ),
                final_vmax_rad_s=float(
                    fast_raw.get("final_vmax_rad_s", 0.35)
                ),
                release_descent_vmax_rad_s=float(
                    fast_raw.get("release_descent_vmax_rad_s", 1.4)
                ),
                release_final_vmax_rad_s=float(
                    fast_raw.get("release_final_vmax_rad_s", 0.8)
                ),
                gripper_open_duration_s=float(
                    fast_raw.get("gripper_open_duration_s", 0.8)
                ),
                gripper_preclose_duration_s=float(
                    fast_raw.get("gripper_preclose_duration_s", 0.35)
                ),
                gripper_grip_duration_s=float(
                    fast_raw.get("gripper_grip_duration_s", 0.5)
                ),
                gripper_release_open_duration_s=float(
                    fast_raw.get("gripper_release_open_duration_s", 0.45)
                ),
                gripper_speed_raw_per_s=float(
                    fast_raw.get("gripper_speed_raw_per_s", 900.0)
                ),
                gripper_min_duration_s=float(
                    fast_raw.get("gripper_min_duration_s", 0.25)
                ),
                gripper_settle_s=float(fast_raw.get("gripper_settle_s", 0.6)),
                verify_settle_s=float(fast_raw.get("verify_settle_s", 0.35)),
                max_total_duration_s=float(
                    fast_raw.get("max_total_duration_s", 6.0)
                ),
                max_points=int(fast_raw.get("max_points", 400)),
            )
            _require(
                0.0 < fast.vmax_rad_s <= 4.0,
                "task.fast.vmax_rad_s must be in (0, 4.0] (the measured ceiling)",
            )
            _require(
                0.0 < fast.amax_rad_s2 <= 20.0,
                "task.fast.amax_rad_s2 must be in (0, 20.0]",
            )
            _require(
                0.025 <= fast.dt_s <= 0.5,
                "task.fast.dt_s must be in [0.025, 0.5] s (the vendor clamps <20 ms)",
            )
            _require(
                0.0 < fast.final_vmax_rad_s <= fast.descent_vmax_rad_s
                <= fast.vmax_rad_s,
                "task.fast descent/final velocities must ascend to vmax",
            )
            _require(
                0.0 < fast.release_final_vmax_rad_s
                <= fast.release_descent_vmax_rad_s
                <= fast.vmax_rad_s,
                "task.fast release velocities must ascend to vmax",
            )
            for name, value in (
                ("gripper_open_duration_s", fast.gripper_open_duration_s),
                ("gripper_preclose_duration_s", fast.gripper_preclose_duration_s),
                ("gripper_grip_duration_s", fast.gripper_grip_duration_s),
                (
                    "gripper_release_open_duration_s",
                    fast.gripper_release_open_duration_s,
                ),
            ):
                _require(
                    fast.gripper_min_duration_s <= value <= 5.0,
                    f"task.fast.{name} must be within the fast gripper duration range",
                )
            _require(
                100.0 <= fast.gripper_speed_raw_per_s <= 1000.0,
                "task.fast.gripper_speed_raw_per_s must be in [100, 1000]",
            )
            _require(
                0.2 <= fast.gripper_min_duration_s <= 1.0,
                "task.fast.gripper_min_duration_s must be in [0.2, 1.0]",
            )
            _require(
                0.2 <= fast.gripper_settle_s <= 2.0,
                "task.fast.gripper_settle_s must be in [0.2, 2.0]",
            )
            _require(
                0.1 <= fast.verify_settle_s <= 1.0,
                "task.fast.verify_settle_s must be in [0.1, 1.0]",
            )
            _require(
                0.0 < fast.max_total_duration_s <= max_duration,
                "task.fast.max_total_duration_s must be within the pose ceiling",
            )
            _require(
                1 <= fast.max_points <= 1000,
                "task.fast.max_points must be in [1, 1000]",
            )
        _require(locate.min_range_m > 0, "task.locate.min_range_m must be positive")
        _require(
            locate.min_range_m < locate.max_range_m,
            "task.locate range must ascend",
        )
        _require(locate.voxel_m > 0, "task.locate.voxel_m must be positive")
        _require(
            locate.min_cluster_points >= 1,
            "task.locate.min_cluster_points must be >= 1",
        )
        _require(
            locate.min_height_m < locate.max_height_m,
            "task.locate height range must ascend",
        )
        grasp_j2 = float(task_raw.get("grasp_j2", -0.6618))
        grasp_j3 = float(task_raw.get("grasp_j3", -1.80117))
        grasp_j5 = float(task_raw.get("grasp_j5", 0.0))
        pre_offset = float(task_raw.get("pre_grasp_offset_rad", 0.15))
        preclose = float(task_raw.get("preclose_rad", -1.6))
        grip = float(task_raw.get("grip_rad", -0.63))
        _require(0.0 <= pre_offset <= 0.5, "task.pre_grasp_offset_rad must be 0..0.5")
        _require(
            gripper.min_rad <= preclose <= gripper.max_rad,
            "task.preclose_rad must be inside the gripper range",
        )
        _require(
            gripper.min_rad <= grip <= gripper.max_rad,
            "task.grip_rad must be inside the gripper range",
        )
        grip_pre_dur = float(task_raw.get("grip_preclose_duration_s", 1.3))
        grip_dur = float(task_raw.get("grip_duration_s", 2.0))
        release_dur = float(task_raw.get("release_open_duration_s", 3.5))
        for name, value in (
            ("grip_preclose_duration_s", grip_pre_dur),
            ("grip_duration_s", grip_dur),
            ("release_open_duration_s", release_dur),
        ):
            _require(
                gripper.min_duration_s <= value <= gripper.max_duration_s,
                f"task.{name} must be inside the gripper duration range",
            )
        verify_radius = float(task_raw.get("verify_radius_m", 0.04))
        verify_height = float(task_raw.get("verify_min_height_m", 0.01))
        min_reach = float(task_raw.get("min_reach_m", 0.10))
        max_reach = float(task_raw.get("max_reach_m", 0.30))
        max_xy = float(task_raw.get("max_xy_error_mm", 3.0))
        _require(verify_radius > 0, "task.verify_radius_m must be positive")
        _require(verify_height >= 0, "task.verify_min_height_m must be >= 0")
        _require(0 <= min_reach < max_reach, "task reach range must ascend")
        _require(max_xy >= 0, "task.max_xy_error_mm must be >= 0")

        forms: list[TaskFormConfig] = []
        forms_raw = task_raw.get("forms")
        if forms_raw is not None:
            _require(
                isinstance(forms_raw, list) and bool(forms_raw),
                "task.forms must be a non-empty list",
            )
            for index, form_raw in enumerate(forms_raw):
                _require(
                    isinstance(form_raw, dict),
                    f"task.forms[{index}] must be a mapping",
                )
                form_id = str(form_raw.get("id", "")).strip()
                _require(bool(form_id), f"task.forms[{index}].id is required")
                _require(
                    all(item.form_id != form_id for item in forms),
                    f"task.forms id {form_id!r} is duplicated",
                )
                label = str(form_raw.get("label", "")).strip()
                label_en = str(form_raw.get("label_en", "")).strip()
                _require(bool(label), f"task.forms[{index}].label is required")
                _require(
                    bool(label_en), f"task.forms[{index}].label_en is required"
                )
                mode = str(form_raw.get("mode", "task")).strip().lower()
                _require(
                    mode in TASK_FORM_MODES,
                    f"task.forms[{index}].mode must be one of {TASK_FORM_MODES}",
                )
                reason = str(form_raw.get("reason", "")).strip()
                if mode in ("pose", "refused"):
                    _require(
                        bool(reason),
                        f"task.forms[{index}].reason is required for mode {mode!r}",
                    )
                top_height = form_raw.get("top_height_mm")
                if top_height is not None:
                    top_height = float(top_height)
                    _require(
                        math.isfinite(top_height) and top_height >= 0,
                        f"task.forms[{index}].top_height_mm must be finite and >= 0",
                    )
                close_register = form_raw.get("close_register")
                if close_register is not None:
                    close_register = int(close_register)
                    _require(
                        0 <= close_register <= 1000,
                        f"task.forms[{index}].close_register must be 0..1000",
                    )
                form_j2 = form_raw.get("grasp_j2")
                form_j3 = form_raw.get("grasp_j3")
                form_j4 = form_raw.get("grasp_j4")
                form_j5 = form_raw.get("grasp_j5")
                for name, value in (
                    ("grasp_j2", form_j2),
                    ("grasp_j3", form_j3),
                    ("grasp_j4", form_j4),
                    ("grasp_j5", form_j5),
                ):
                    if value is None:
                        continue
                    value = float(value)
                    _require(
                        math.isfinite(value) and -2.0 <= value <= 2.0,
                        f"task.forms[{index}].{name} must be finite and in [-2.0, 2.0]",
                    )
                    if name == "grasp_j2":
                        form_j2 = value
                    elif name == "grasp_j3":
                        form_j3 = value
                    elif name == "grasp_j4":
                        form_j4 = value
                    else:
                        form_j5 = value
                form_preclose = form_raw.get("preclose_rad")
                form_grip = form_raw.get("grip_rad")
                for name, value in (
                    ("preclose_rad", form_preclose),
                    ("grip_rad", form_grip),
                ):
                    if value is None:
                        continue
                    value = float(value)
                    _require(
                        math.isfinite(value)
                        and gripper.min_rad <= value <= gripper.max_rad,
                        f"task.forms[{index}].{name} must be inside the gripper range",
                    )
                    if name == "preclose_rad":
                        form_preclose = value
                    else:
                        form_grip = value
                form_correction = form_raw.get("correction_mm")
                if form_correction is not None:
                    form_correction = _triple(
                        form_correction,
                        f"task.forms[{index}].correction_mm",
                    )
                base_raw = form_raw.get("base_point_m")
                base_point = None
                if base_raw is not None:
                    _require(
                        isinstance(base_raw, (list, tuple))
                        and len(base_raw) == 2,
                        f"task.forms[{index}].base_point_m must have 2 values",
                    )
                    base_point = (float(base_raw[0]), float(base_raw[1]))
                    _require(
                        all(math.isfinite(value) for value in base_point)
                        and base_point[0] > 0.0,
                        f"task.forms[{index}].base_point_m must be finite with x > 0",
                    )
                base_tolerance = float(
                    form_raw.get("base_tolerance_m", 0.012)
                )
                _require(
                    0.005 <= base_tolerance <= 0.05,
                    f"task.forms[{index}].base_tolerance_m must be in [0.005, 0.05]",
                )
                recorded_raw = form_raw.get("recorded_pose")
                recorded_pose = None
                if recorded_raw is not None:
                    _require(
                        isinstance(recorded_raw, dict),
                        f"task.forms[{index}].recorded_pose must be a mapping",
                    )
                    _require(
                        set(recorded_raw) == set(ARM_JOINTS),
                        f"task.forms[{index}].recorded_pose must have the five arm joints",
                    )
                    recorded_pose = {
                        name: float(recorded_raw[name]) for name in ARM_JOINTS
                    }
                    _require(
                        all(
                            math.isfinite(value)
                            for value in recorded_pose.values()
                        ),
                        f"task.forms[{index}].recorded_pose must be finite",
                    )
                _require(
                    mode != "pose" or recorded_pose is not None,
                    f"task.forms[{index}] mode 'pose' needs recorded_pose",
                )
                forms.append(
                    TaskFormConfig(
                        form_id=form_id,
                        label=label,
                        label_en=label_en,
                        mode=mode,
                        reason=reason,
                        size_text=str(form_raw.get("size_text", "")).strip(),
                        top_height_mm=top_height,
                        close_register=close_register,
                        grasp_j2=form_j2,
                        grasp_j3=form_j3,
                        grasp_j4=form_j4,
                        grasp_j5=form_j5,
                        preclose_rad=form_preclose,
                        grip_rad=form_grip,
                        correction_mm=form_correction,
                        base_point_m=base_point,
                        base_tolerance_m=base_tolerance,
                        recorded_pose=recorded_pose,
                        notes=str(form_raw.get("notes", "")).strip(),
                        catalog=str(
                            form_raw.get(
                                "catalog", "docs/phase2/OBJECT_GRASP_CATALOG.md"
                            )
                        ),
                    )
                )
        else:
            # Backward-compatible contract: no form list means the original
            # 39.4 mm cube only.
            forms = [
                TaskFormConfig(
                    form_id="cube_39",
                    label="39.4 mm 木块",
                    label_en="39.4 mm cube",
                    mode="task",
                    size_text="39.4 × 39.4 × 39.4 mm",
                    top_height_mm=39.4,
                    close_register=414,
                    notes="G8 原始标定对象；j4 由深位拟合给出，闭合 −0.63。",
                )
            ]
        default_form = str(
            task_raw.get("default_form") or forms[0].form_id
        ).strip()
        _require(
            any(item.form_id == default_form for item in forms),
            f"task.default_form {default_form!r} is not a task form",
        )
        default_match = next(
            item for item in forms if item.form_id == default_form
        )
        _require(
            default_match.mode == "task",
            "task.default_form must be a 'task' form",
        )

        task = TaskConfig(
            hand_eye_path=str(hand_eye_path),
            correction_mm=correction,
            view_preset=view_preset,
            lift_preset=lift_preset,
            grasp_j2=grasp_j2,
            grasp_j3=grasp_j3,
            grasp_j5=grasp_j5,
            pre_grasp_offset_rad=pre_offset,
            preclose_rad=preclose,
            grip_rad=grip,
            grip_preclose_duration_s=grip_pre_dur,
            grip_duration_s=grip_dur,
            release_open_duration_s=release_dur,
            verify_radius_m=verify_radius,
            verify_min_height_m=verify_height,
            min_reach_m=min_reach,
            max_reach_m=max_reach,
            max_xy_error_mm=max_xy,
            locate=locate,
            approach=approach,
            fast=fast,
            forms=tuple(forms),
            default_form=default_form,
        )

    canonical = {
        "schema_version": schema_version,
        "scope": scope,
        "max_velocity_rad_s": velocity,
        "min_duration_s": min_duration,
        "max_duration_s": max_duration,
        "path_samples": path_samples,
        "completion_tolerance_pulses": tolerance,
        "completion_settle_samples": settle,
        "timeout_margin_s": margin,
        "gripper": gripper.to_public_dict(),
        "workspace": workspace.to_public_dict(),
        "table_plane": table.to_public_dict(),
        "chassis_guard": chassis.to_public_dict(),
        "presets": [item.to_public_dict() for item in presets],
        "task": task.to_public_dict() if task is not None else None,
    }
    config_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return TakeoverConfig(
        schema_version=schema_version,
        scope=scope,
        max_velocity_rad_s=velocity,
        min_duration_s=min_duration,
        max_duration_s=max_duration,
        path_samples=path_samples,
        completion_tolerance_pulses=tolerance,
        completion_settle_samples=settle,
        timeout_margin_s=margin,
        gripper=gripper,
        workspace=workspace,
        table_plane=table,
        chassis_guard=chassis,
        presets=tuple(presets),
        task=task,
        source_path=str(path),
        config_hash=config_hash,
    )
