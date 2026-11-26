from __future__ import annotations

import math

import pytest

from app.domain.enums import ErrorCode
from app.domain.mapping import MappingConfig
from app.domain.models import MotionTarget
from app.domain.motion import (
    SUPERVISED_MAX_STEP_RAD,
    effective_motion_limits,
)
from app.safety.validation import validate_targets


def _codes(outcome) -> list[str]:
    return [issue.code for issue in outcome.errors]


def test_valid_single_joint(mapping: MappingConfig) -> None:
    outcome = validate_targets(
        mapping,
        [MotionTarget("joint1", 0.1, 1.0)],
        current_targets={"joint1": 0.0},
    )
    assert outcome.valid
    assert outcome.normalized[0]["target_raw"] == 524
    assert outcome.normalized[0]["limit_verified"] is True
    assert not any(
        issue.code == "MAPPING_PROVISIONAL" for issue in outcome.warnings
    )


def test_unknown_joint_rejected(mapping: MappingConfig) -> None:
    outcome = validate_targets(mapping, [MotionTarget("joint9", 0.1, 1.0)])
    assert not outcome.valid
    assert ErrorCode.UNKNOWN_JOINT.value in _codes(outcome)


def test_gripper_excluded(mapping: MappingConfig) -> None:
    outcome = validate_targets(mapping, [MotionTarget("r_joint", 0.1, 1.0)])
    assert not outcome.valid
    assert ErrorCode.GRIPPER_NOT_IN_G1.value in _codes(outcome)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_rejected(mapping: MappingConfig, bad: float) -> None:
    outcome = validate_targets(mapping, [MotionTarget("joint1", bad, 1.0)])
    assert not outcome.valid
    assert ErrorCode.NON_FINITE_TARGET.value in _codes(outcome)


def test_joint3_positive_rejected(mapping: MappingConfig) -> None:
    outcome = validate_targets(mapping, [MotionTarget("joint3", 0.1, 1.0)])
    assert not outcome.valid
    assert ErrorCode.OUT_OF_LIMIT.value in _codes(outcome)


def test_step_limit_rejected(mapping: MappingConfig) -> None:
    outcome = validate_targets(
        mapping,
        [MotionTarget("joint1", 0.5, 1.0)],
        current_targets={"joint1": 0.0},
    )
    assert not outcome.valid
    assert ErrorCode.STEP_EXCEEDED.value in _codes(outcome)


@pytest.mark.parametrize("bad_duration", [0.1, 11.0, math.nan])
def test_duration_range_rejected(mapping: MappingConfig, bad_duration: float) -> None:
    outcome = validate_targets(mapping, [MotionTarget("joint1", 0.1, bad_duration)])
    assert not outcome.valid
    assert ErrorCode.DURATION_RANGE.value in _codes(outcome)


def test_duplicate_joint_rejected(mapping: MappingConfig) -> None:
    outcome = validate_targets(
        mapping,
        [MotionTarget("joint1", 0.1, 1.0), MotionTarget("joint1", 0.2, 1.0)],
    )
    assert not outcome.valid
    assert ErrorCode.DUPLICATE_JOINT.value in _codes(outcome)


def test_empty_targets_rejected(mapping: MappingConfig) -> None:
    outcome = validate_targets(mapping, [])
    assert not outcome.valid
    assert ErrorCode.NO_TARGETS.value in _codes(outcome)


def test_joint3_negative_allowed(mapping: MappingConfig) -> None:
    outcome = validate_targets(
        mapping,
        [MotionTarget("joint3", -0.1, 1.0)],
        current_targets={"joint3": 0.0},
    )
    assert outcome.valid
    assert outcome.normalized[0]["target_raw"] == 524


def test_supervised_limits_align_to_a_whole_pulse(mapping: MappingConfig) -> None:
    """A target accepted by the backend must not need one extra gateway pulse."""
    limits = effective_motion_limits(mapping, supervised=True)
    pulses = limits.max_step_rad / mapping.radians_per_tick
    assert pulses == pytest.approx(round(pulses), abs=1e-9)
    assert limits.max_step_rad <= SUPERVISED_MAX_STEP_RAD
    assert limits.min_duration_s >= 1.0
    assert limits.single_joint_only is True


def test_supervised_rejects_multi_joint(mapping: MappingConfig) -> None:
    limits = effective_motion_limits(mapping, supervised=True)
    outcome = validate_targets(
        mapping,
        [MotionTarget("joint1", 0.05, 1.0), MotionTarget("joint2", 0.05, 1.0)],
        current_targets={"joint1": 0.0, "joint2": 0.0},
        max_step_rad=limits.max_step_rad,
        min_duration_s=limits.min_duration_s,
        single_joint_only=limits.single_joint_only,
    )
    assert not outcome.valid
    assert ErrorCode.SUPERVISED_SINGLE_JOINT.value in _codes(outcome)


def test_supervised_rejects_short_duration(mapping: MappingConfig) -> None:
    limits = effective_motion_limits(mapping, supervised=True)
    outcome = validate_targets(
        mapping,
        [MotionTarget("joint1", 0.05, 0.5)],
        current_targets={"joint1": 0.0},
        max_step_rad=limits.max_step_rad,
        min_duration_s=limits.min_duration_s,
        single_joint_only=limits.single_joint_only,
    )
    assert not outcome.valid
    assert ErrorCode.DURATION_RANGE.value in _codes(outcome)


def test_supervised_pulse_aligned_step_is_accepted(mapping: MappingConfig) -> None:
    limits = effective_motion_limits(mapping, supervised=True)
    target = limits.max_step_rad
    outcome = validate_targets(
        mapping,
        [MotionTarget("joint1", target, 1.0)],
        current_targets={"joint1": 0.0},
        max_step_rad=limits.max_step_rad,
        min_duration_s=limits.min_duration_s,
        single_joint_only=limits.single_joint_only,
    )
    assert outcome.valid, _codes(outcome)
    assert outcome.normalized[0]["target_raw"] == 523


def test_preview_token_payload_excludes_live_from_rad(service) -> None:
    """The token binds the requested command, not the state snapshot.

    Hardware command echo can move by small amounts between preview and
    execute. Including ``from_rad`` would reject a safe command as a payload
    mismatch before the safety gate could evaluate it.
    """
    targets = service._token_targets(
        [
            {
                "joint": "joint1",
                "from_rad": 0.123,
                "target_rad": 0.2,
                "duration_s": 1.0,
                "target_raw": 524,
            }
        ]
    )
    assert targets == [
        {
            "joint": "joint1",
            "target_rad": 0.2,
            "duration_s": 1.0,
            "target_raw": 524,
        }
    ]
