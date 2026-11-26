"""Pure S-curve profile tests (no robot)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from app.domain.profile import (
    ProfileParams,
    plan_move,
    segment_velocities,
    validate_params,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = Path(__file__).resolve().parents[2] / "tools" / "phase2"


def _reference_module():
    """Import tools/phase2/motion_profile.py by path, not from the repo root."""
    if not (REFERENCE / "motion_profile.py").is_file():
        pytest.skip("the phase-2 reference profile is not in this checkout")
    if str(REFERENCE) not in sys.path:
        sys.path.insert(0, str(REFERENCE))
    import motion_profile  # type: ignore[import-not-found]

    return motion_profile


def test_endpoints_are_exact_and_every_segment_is_short() -> None:
    start = {"joint1": 0.0, "joint2": 0.2574, "joint3": -1.18773, "joint4": -1.5917}
    target = {"joint1": 0.03, "joint2": -0.6618, "joint3": -1.80117, "joint4": 0.0}
    plan = plan_move(start, target, ProfileParams(4.0, 20.0))
    assert plan.segments == len(plan.points) == len(plan.durations_s)
    assert plan.points[-1] == pytest.approx(target)
    assert plan.total_s == pytest.approx(sum(plan.durations_s))
    assert all(0.0 < duration <= 0.05 + 1e-9 for duration in plan.durations_s)
    # The largest joint span is 1.59 rad; the peak is the commanded vmax.
    assert plan.v_peak == pytest.approx(4.0)
    assert plan.peak_velocity_rad_s["joint4"] == pytest.approx(4.0)


def test_matches_the_hardware_verified_reference_profile() -> None:
    reference = _reference_module()
    start = [0.0, 0.2574, -1.18773, -1.70, -0.01676]
    target = [0.03, -0.6618, -1.80117, 0.0, 0.0]
    times, points, info = reference.plan_move(start, target, 4.0, 20.0, 0.05)
    plan = plan_move(
        {f"joint{index + 1}": value for index, value in enumerate(start)},
        {f"joint{index + 1}": value for index, value in enumerate(target)},
        ProfileParams(4.0, 20.0, 0.05),
    )
    assert plan.total_s == pytest.approx(info["total"])
    assert plan.segments == info["segments"]
    # The reference includes the t=0 point; ours starts at the first segment.
    for index, point in enumerate(plan.points):
        expected = points[index + 1]
        got = [point[f"joint{number + 1}"] for number in range(5)]
        assert got == pytest.approx(expected, abs=1e-9)


def test_short_move_is_triangular_and_still_synchronised() -> None:
    start = {"joint1": 0.0, "joint2": 0.0}
    target = {"joint1": 0.02, "joint2": 0.01}
    plan = plan_move(start, target, ProfileParams(4.0, 20.0))
    assert plan.v_peak < 4.0
    # Both joints finish together: the ratio is the displacement ratio.
    last = plan.points[-1]
    assert last["joint1"] / last["joint2"] == pytest.approx(2.0)
    assert plan.segments >= 1


def test_zero_move_emits_one_hold_segment() -> None:
    plan = plan_move({"joint1": 0.1}, {"joint1": 0.1}, ProfileParams(4.0, 20.0))
    assert plan.segments == 1
    assert plan.total_s == pytest.approx(0.05)
    assert plan.points == [{"joint1": 0.1}]
    assert plan.v_peak == 0.0


def test_segment_velocities_track_the_profile() -> None:
    start = {"joint1": 0.0}
    target = {"joint1": 1.5}
    plan = plan_move(start, target, ProfileParams(4.0, 20.0))
    velocities = segment_velocities(start, plan.points, plan.durations_s)
    assert max(velocities["joint1"]) <= 4.0 + 1e-9
    assert max(velocities["joint1"]) == pytest.approx(4.0, abs=0.02)


def test_invalid_params_are_rejected() -> None:
    with pytest.raises(ValueError):
        validate_params(ProfileParams(0.0, 20.0))
    with pytest.raises(ValueError):
        validate_params(ProfileParams(4.0, 0.0))
    with pytest.raises(ValueError):
        validate_params(ProfileParams(4.0, 20.0, dt_s=0.01))
    with pytest.raises(ValueError):
        plan_move({"joint1": 0.0}, {"joint2": 0.0}, ProfileParams(4.0, 20.0))


def test_misaligned_joint_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        plan_move({"joint1": 0.0}, {}, ProfileParams(4.0, 20.0))
