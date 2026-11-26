"""Backend-side target validation.

Every motion request is validated here, before any adapter dispatch. The
vendor stack silently clamps out-of-range values; this layer rejects them.

Three write scopes share this module:

``step``
    G6: exactly one arm joint, a small step cap.
``pose``
    G7: exactly the five arm joints (one whole configuration) **or** exactly
    the gripper. The five-joint form adds a velocity cap; the gripper form uses
    its measured command range and the vendor's pulse/s tracking speed.
``standard``
    Mock/preview: the mapping's own limits, any number of joints.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from ..domain.enums import ErrorCode
from ..domain.mapping import JointMapping, MappingConfig
from ..domain.models import MotionTarget, ValidationIssue
from ..domain.takeover import GripperLimit


@dataclass
class ValidationOutcome:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    normalized: list[dict] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def _issue(code: ErrorCode | str, message: str, joint: str | None = None, **details) -> ValidationIssue:
    # Callers may pass `details={...}` for readability; flatten it so the API
    # does not expose a nested {"details": {"details": ...}} shape.
    nested = details.pop("details", None)
    if isinstance(nested, dict):
        details.update(nested)
    code_value = code.value if isinstance(code, ErrorCode) else str(code)
    return ValidationIssue(code=code_value, message=message, joint=joint, details=details)


def _raw_bounds(mapping: JointMapping) -> tuple[int, int]:
    return min(mapping.min_raw, mapping.max_raw), max(mapping.min_raw, mapping.max_raw)


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _check_arm_target(
    outcome: ValidationOutcome,
    mapping: MappingConfig,
    target: MotionTarget,
    *,
    from_rad: float,
    step_limit: float,
    duration_floor: float,
    max_duration_s: float,
    velocity_limit_rad_s: float | None,
    completion: str = "position",
) -> None:
    name = (target.joint or "").strip()
    joint = mapping.get_joint(name)
    if joint is None:  # guarded by the caller
        outcome.errors.append(
            _issue(ErrorCode.UNKNOWN_JOINT, f"unknown joint: {name}", name)
        )
        return

    if not _finite(target.target_rad):
        outcome.errors.append(
            _issue(
                ErrorCode.NON_FINITE_TARGET,
                f"{name}: target must be a finite number",
                name,
                value=repr(target.target_rad),
            )
        )
        return
    target_rad = float(target.target_rad)

    if target_rad < joint.min_rad or target_rad > joint.max_rad:
        outcome.errors.append(
            _issue(
                ErrorCode.OUT_OF_LIMIT,
                f"{name}: {target_rad:.6f} rad outside verified limit "
                f"[{joint.min_rad:.6f}, {joint.max_rad:.6f}]",
                name,
                target_rad=target_rad,
                min_rad=joint.min_rad,
                max_rad=joint.max_rad,
            )
        )
        return

    delta_rad = target_rad - from_rad
    if abs(delta_rad) > step_limit + 1e-12:
        outcome.errors.append(
            _issue(
                ErrorCode.STEP_EXCEEDED,
                f"{name}: step {delta_rad:.6f} rad exceeds max_step_rad "
                f"{step_limit:.6f}",
                name,
                delta_rad=delta_rad,
                max_step_rad=step_limit,
            )
        )
        return

    duration = target.duration_s
    if (
        not _finite(duration)
        or float(duration) < duration_floor
        or float(duration) > max_duration_s
    ):
        outcome.errors.append(
            _issue(
                ErrorCode.DURATION_RANGE,
                f"{name}: duration must be within "
                f"[{duration_floor}, {max_duration_s}] s",
                name,
                duration_s=repr(duration),
            )
        )
        return

    duration = float(duration)
    if velocity_limit_rad_s is not None:
        velocity = abs(delta_rad) / duration
        if velocity > velocity_limit_rad_s + 1e-12:
            outcome.errors.append(
                _issue(
                    ErrorCode.VELOCITY_EXCEEDED,
                    f"{name}: {velocity:.3f} rad/s exceeds the pose velocity "
                    f"cap {velocity_limit_rad_s:.3f} rad/s; increase duration "
                    f"to at least {abs(delta_rad) / velocity_limit_rad_s:.2f} s",
                    name,
                    velocity_rad_s=velocity,
                    max_velocity_rad_s=velocity_limit_rad_s,
                    delta_rad=delta_rad,
                )
            )
            return

    raw_float = joint.rad_to_raw(target_rad)
    raw_int = int(round(raw_float))
    raw_low, raw_high = _raw_bounds(joint)
    if raw_int < raw_low or raw_int > raw_high:
        outcome.errors.append(
            _issue(
                ErrorCode.OUT_OF_LIMIT,
                f"{name}: mapped raw {raw_int} outside [{raw_low}, {raw_high}]",
                name,
                raw_position=raw_int,
            )
        )
        return

    if abs(delta_rad) > step_limit * 0.8:
        outcome.warnings.append(
            _issue(
                "LARGE_STEP",
                f"{name}: step is close to the configured maximum",
                name,
                delta_rad=delta_rad,
            )
        )
    if abs(target_rad - joint.min_rad) < 0.02 or abs(target_rad - joint.max_rad) < 0.02:
        outcome.warnings.append(
            _issue(
                "NEAR_LIMIT",
                f"{name}: target is within 0.02 rad of a limit",
                name,
            )
        )

    outcome.normalized.append(
        {
            "joint": name,
            "servo_id": joint.servo_id,
            "from_rad": from_rad,
            "target_rad": target_rad,
            "delta_rad": delta_rad,
            "duration_s": duration,
            "target_raw": raw_int,
            "limit_min_rad": joint.min_rad,
            "limit_max_rad": joint.max_rad,
            "limit_verified": joint.verified,
            "limit_source": joint.limit_source,
            "completion": completion,
        }
    )


def _check_gripper_target(
    outcome: ValidationOutcome,
    gripper: GripperLimit,
    target: MotionTarget,
    *,
    from_rad: float,
    duration_floor: float,
    max_duration_s: float,
) -> None:
    if not _finite(target.target_rad):
        outcome.errors.append(
            _issue(
                ErrorCode.NON_FINITE_TARGET,
                f"{gripper.joint}: target must be a finite number",
                gripper.joint,
                value=repr(target.target_rad),
            )
        )
        return
    target_rad = float(target.target_rad)
    if target_rad < gripper.min_rad or target_rad > gripper.max_rad:
        outcome.errors.append(
            _issue(
                ErrorCode.GRIPPER_RANGE,
                f"{gripper.joint}: {target_rad:.6f} rad outside the measured "
                f"command range [{gripper.min_rad:.6f}, {gripper.max_rad:.6f}] "
                f"(open raw {gripper.open_raw}, close raw {gripper.close_raw})",
                gripper.joint,
                target_rad=target_rad,
                min_rad=gripper.min_rad,
                max_rad=gripper.max_rad,
            )
        )
        return

    duration = target.duration_s
    floor = max(float(duration_floor), gripper.min_duration_s)
    ceiling = min(float(max_duration_s), gripper.max_duration_s)
    if not _finite(duration) or float(duration) < floor or float(duration) > ceiling:
        outcome.errors.append(
            _issue(
                ErrorCode.DURATION_RANGE,
                f"{gripper.joint}: duration must be within [{floor}, {ceiling}] s",
                gripper.joint,
                duration_s=repr(duration),
            )
        )
        return
    duration = float(duration)

    from_raw = gripper.rad_to_raw(from_rad)
    target_raw = gripper.rad_to_raw(target_rad)
    delta_raw = target_raw - from_raw
    velocity = abs(delta_raw) / duration
    if velocity > gripper.speed_raw_per_s + 1e-9:
        outcome.errors.append(
            _issue(
                ErrorCode.VELOCITY_EXCEEDED,
                f"{gripper.joint}: {velocity:.1f} raw/s exceeds the measured "
                f"{gripper.speed_raw_per_s:.0f} raw/s tracking speed; increase "
                f"duration to at least "
                f"{abs(delta_raw) / gripper.speed_raw_per_s:.2f} s",
                gripper.joint,
                velocity_raw_per_s=velocity,
                max_velocity_raw_per_s=gripper.speed_raw_per_s,
            )
        )
        return

    outcome.normalized.append(
        {
            "joint": gripper.joint,
            "servo_id": gripper.servo_id,
            "from_rad": from_rad,
            "target_rad": target_rad,
            "delta_rad": target_rad - from_rad,
            "duration_s": duration,
            "target_raw": int(round(target_raw)),
            "limit_min_rad": gripper.min_rad,
            "limit_max_rad": gripper.max_rad,
            "limit_verified": True,
            "limit_source": "measured_gripper_stops_2026_11_18",
            "completion": gripper.completion,
        }
    )
    if abs(target_rad - gripper.min_rad) < 0.02 or abs(target_rad - gripper.max_rad) < 0.02:
        outcome.warnings.append(
            _issue(
                "NEAR_LIMIT",
                f"{gripper.joint}: target is at a measured gripper stop",
                gripper.joint,
            )
        )


def validate_targets(
    mapping: MappingConfig,
    targets: Iterable[MotionTarget],
    *,
    current_targets: dict[str, float] | None = None,
    max_step_rad: float | None = None,
    min_duration_s: float | None = None,
    max_duration_s: float | None = None,
    single_joint_only: bool = False,
    scope: str = "step",
    max_velocity_rad_s: float | None = None,
    gripper: GripperLimit | None = None,
) -> ValidationOutcome:
    """Validate targets against the mapping and effective motion limits.

    ``max_step_rad``/``min_duration_s`` override the mapping's safety block so
    supervised commissioning can run narrower limits without editing the
    mapping file. ``single_joint_only`` rejects multi-joint commands before any
    per-joint work, which is how the supervised path keeps one hand on one
    joint at a time.

    In ``pose`` scope the command is either one whole five-joint configuration
    or one gripper command; nothing in between.
    """
    outcome = ValidationOutcome()
    current = current_targets or {}
    seen: set[str] = set()
    items = list(targets)
    step_limit = mapping.safety.max_step_rad if max_step_rad is None else max_step_rad
    duration_floor = (
        mapping.safety.min_duration_s if min_duration_s is None else min_duration_s
    )
    duration_ceiling = (
        mapping.safety.max_duration_s if max_duration_s is None else max_duration_s
    )

    if not items:
        outcome.errors.append(_issue(ErrorCode.NO_TARGETS, "at least one target is required"))
        return outcome

    pose_scope = scope == "pose"

    if pose_scope:
        names = [(target.joint or "").strip() for target in items]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            for name in sorted(duplicates):
                outcome.errors.append(
                    _issue(
                        ErrorCode.DUPLICATE_JOINT,
                        f"joint {name} appears more than once",
                        name,
                    )
                )
            return outcome
        gripper_only = (
            gripper is not None
            and len(items) == 1
            and names[0] == gripper.joint
        )
        arm_set = set(mapping.joints)
        if gripper_only:
            from_rad = current.get(gripper.joint)
            if from_rad is None:
                from_rad = gripper.open_rad
                outcome.warnings.append(
                    _issue(
                        "GRIPPER_BASELINE_ASSUMED",
                        f"{gripper.joint}: no measured gripper position; "
                        "using the open command as the velocity baseline",
                        gripper.joint,
                    )
                )
            _check_gripper_target(
                outcome,
                gripper,
                items[0],
                from_rad=float(from_rad),
                duration_floor=duration_floor,
                max_duration_s=duration_ceiling,
            )
            return outcome
        if set(names) != arm_set:
            missing = sorted(arm_set - set(names))
            extra = sorted(set(names) - arm_set)
            outcome.errors.append(
                _issue(
                    ErrorCode.POSE_JOINT_SET,
                    "pose scope accepts exactly the five arm joints or exactly "
                    f"the gripper ({gripper.joint if gripper else 'n/a'}); "
                    f"missing={missing}, extra={extra}",
                    details={"missing": missing, "extra": extra},
                )
            )
            return outcome
    elif single_joint_only and len(items) != 1:
        outcome.errors.append(
            _issue(
                ErrorCode.SUPERVISED_SINGLE_JOINT,
                "supervised motion accepts exactly one joint per command",
                details={"joint_count": len(items)},
            )
        )
        return outcome

    for target in items:
        name = (target.joint or "").strip()
        if not name:
            outcome.errors.append(_issue(ErrorCode.BAD_REQUEST, "joint name is required"))
            continue
        if name in seen:
            outcome.errors.append(
                _issue(ErrorCode.DUPLICATE_JOINT, f"joint {name} appears more than once", name)
            )
            continue
        seen.add(name)

        if not pose_scope and name in {item.get("name") for item in mapping.excluded}:
            outcome.errors.append(
                _issue(
                    ErrorCode.GRIPPER_NOT_IN_G1,
                    f"joint {name} is excluded from this scope",
                    name,
                )
            )
            continue

        joint = mapping.get_joint(name)
        if joint is None:
            outcome.errors.append(
                _issue(ErrorCode.UNKNOWN_JOINT, f"unknown joint: {name}", name)
            )
            continue

        from_rad = float(current.get(name, 0.0))
        _check_arm_target(
            outcome,
            mapping,
            target,
            from_rad=from_rad,
            step_limit=step_limit,
            duration_floor=duration_floor,
            max_duration_s=duration_ceiling,
            velocity_limit_rad_s=max_velocity_rad_s,
            completion="position",
        )

    if not mapping.verified:
        outcome.warnings.append(
            _issue(
                "MAPPING_PROVISIONAL",
                "joint mapping is provisional; limits are not hardware-verified",
            )
        )

    return outcome
