"""Forward-kinematics gate for a whole-pose takeover command.

The Studio does not run MoveIt, so it has no collision mesh for the real
linkage. It does have the audited URDF and the measured table plane, and it can
sample the exact straight joint-space path the vendor interpolates. This module
applies the three checks that are supported by measured evidence:

1. the ``armpi_safety`` workspace box on every sampled waypoint,
2. the measured tilted table plane with its 10 mm clearance,
3. the measured low-pose yaw rule (the arm grazes the chassis when joint1 is
   yawed below the 18 cm pose).

It is a **gate**, not a planner. A pass means the nominal TCP stays inside the
documented envelope; it does not prove self-collision freedom. The first
sample (the measured start) is exempt from blocking so an arm that is already
outside the envelope can always be commanded back; its state is still
reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..domain.mapping import MappingConfig
from ..domain.takeover import ARM_JOINTS, TakeoverConfig
from ..domain.visual import UrdfModel, compute_frames

TCP_LINK = "end_effector_link"


@dataclass
class PoseGateViolation:
    code: str
    message: str
    joint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "joint": self.joint,
            "details": self.details,
        }


@dataclass
class PoseCheckOutcome:
    ok: bool
    tcp_m: list[float] | None
    table_clearance_m: float | None
    samples: int
    violations: list[PoseGateViolation]
    start_tcp_m: list[float] | None
    start_table_clearance_m: float | None
    start_compliant: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tcp_m": [round(value, 6) for value in self.tcp_m]
            if self.tcp_m is not None
            else None,
            "table_clearance_m": (
                round(self.table_clearance_m, 6)
                if self.table_clearance_m is not None
                else None
            ),
            "samples": self.samples,
            "start_tcp_m": (
                [round(value, 6) for value in self.start_tcp_m]
                if self.start_tcp_m is not None
                else None
            ),
            "start_table_clearance_m": (
                round(self.start_table_clearance_m, 6)
                if self.start_table_clearance_m is not None
                else None
            ),
            "start_compliant": self.start_compliant,
            "violations": [item.to_public_dict() for item in self.violations],
        }


class PoseGate:
    def __init__(self, config: TakeoverConfig, model: UrdfModel) -> None:
        self.config = config
        self.model = model

    # ------------------------------------------------------------------- FK
    def tcp(self, joints: dict[str, float]) -> tuple[float, float, float] | None:
        result = compute_frames(self.model, dict(joints), {})
        for frame in result["frames"]:
            if frame["name"] == TCP_LINK:
                position = frame["position_m"]
                return (
                    float(position[0]),
                    float(position[1]),
                    float(position[2]),
                )
        return None

    # --------------------------------------------------------------- checks
    def check(
        self,
        start: dict[str, float],
        target: dict[str, float],
        *,
        exempt_start: bool = True,
    ) -> PoseCheckOutcome:
        samples = max(2, int(self.config.path_samples))
        violations: list[PoseGateViolation] = []
        seen: dict[str, int] = {}
        min_clearance: float | None = None
        start_tcp: tuple[float, float, float] | None = None
        start_clearance: float | None = None
        target_tcp: tuple[float, float, float] | None = None

        def add(code: str, message: str, *, joint: str | None = None, details=None):
            count = seen.get(code, 0)
            seen[code] = count + 1
            if count >= 3:
                return
            violations.append(
                PoseGateViolation(
                    code=code,
                    message=message,
                    joint=joint,
                    details=details or {},
                )
            )

        for index in range(samples):
            t = index / (samples - 1)
            positions = {
                name: float(start[name]) + (float(target[name]) - float(start[name])) * t
                for name in ARM_JOINTS
            }
            tcp = self.tcp(positions)
            if tcp is None:
                add(
                    "FK_UNAVAILABLE",
                    f"URDF has no {TCP_LINK} frame; the pose gate cannot run",
                )
                break
            if index == 0:
                start_tcp = tcp
            if index == samples - 1:
                target_tcp = tcp
            clearance: float | None = None
            if self.config.table_plane.enabled:
                clearance = self.config.table_plane.height(tcp)
                if index != 0 or not exempt_start:
                    if clearance < self.config.table_plane.clearance_m:
                        add(
                            "TABLE_CLEARANCE",
                            (
                                f"TCP is {clearance * 1000.0:.1f} mm above the "
                                f"table plane, below the {self.config.table_plane.clearance_m * 1000.0:.0f} mm "
                                "clearance"
                            ),
                            details={
                                "sample": index,
                                "t": round(t, 4),
                                "tcp_m": [round(value, 6) for value in tcp],
                                "clearance_m": round(clearance, 6),
                            },
                        )
            if index != 0 or not exempt_start:
                if self.config.workspace.enabled and not self.config.workspace.contains(tcp):
                    add(
                        "WORKSPACE",
                        (
                            "TCP leaves the workspace box "
                            f"{list(self.config.workspace.lower)} … "
                            f"{list(self.config.workspace.upper)}"
                        ),
                        details={
                            "sample": index,
                            "t": round(t, 4),
                            "tcp_m": [round(value, 6) for value in tcp],
                        },
                    )
                chassis = self.config.chassis_guard.violation(
                    tcp, positions["joint1"]
                )
                if chassis is not None:
                    add(
                        "LOW_POSE_YAW",
                        f"low-pose yaw guard: {chassis}",
                        joint="joint1",
                        details={"sample": index, "t": round(t, 4)},
                    )
            if index == 0:
                start_clearance = clearance
            elif clearance is not None:
                min_clearance = (
                    clearance
                    if min_clearance is None
                    else min(min_clearance, clearance)
                )
        if min_clearance is None:
            min_clearance = start_clearance

        start_compliant = True
        if start_tcp is not None:
            start_violations: list[str] = []
            if self.config.workspace.enabled and not self.config.workspace.contains(
                start_tcp
            ):
                start_violations.append("WORKSPACE")
            if (
                self.config.table_plane.enabled
                and start_clearance is not None
                and start_clearance < self.config.table_plane.clearance_m
            ):
                start_violations.append("TABLE_CLEARANCE")
            if (
                self.config.chassis_guard.violation(
                    start_tcp, float(start["joint1"])
                )
                is not None
            ):
                start_violations.append("LOW_POSE_YAW")
            start_compliant = not start_violations

        return PoseCheckOutcome(
            ok=not violations,
            tcp_m=list(target_tcp) if target_tcp is not None else None,
            table_clearance_m=min_clearance,
            samples=samples,
            violations=violations,
            start_tcp_m=list(start_tcp) if start_tcp is not None else None,
            start_table_clearance_m=start_clearance,
            start_compliant=start_compliant,
        )

    def check_pose(self, joints: dict[str, float]) -> PoseCheckOutcome:
        """Check one static pose (used by the preset list)."""
        return self.check(joints, joints, exempt_start=False)


def mapping_targets_valid(
    mapping: MappingConfig,
    joints: dict[str, float],
    *,
    tolerance: float = 1e-6,
) -> list[str]:
    """Return joint names whose preset value is outside the adopted limits."""
    bad: list[str] = []
    for name in ARM_JOINTS:
        joint = mapping.get_joint(name)
        value = float(joints[name])
        if joint is None or not math.isfinite(value):
            bad.append(name)
            continue
        if value < joint.min_rad - tolerance or value > joint.max_rad + tolerance:
            bad.append(name)
    return bad
