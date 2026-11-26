"""G8.1 chassis approach planning tests (pure)."""

from __future__ import annotations

import pytest

from app.domain.approach import plan_approach
from app.domain.takeover import ApproachConfig


def _config(**overrides) -> ApproachConfig:
    base = dict(
        enabled=True,
        target_point_m=(0.230, 0.0),
        tolerance_m=0.008,
        duty=10.0,
        speed_mm_s_per_duty=4.9,
        min_duration_s=0.25,
        max_duration_s=2.0,
        max_pulses=3,
        settle_s=0.5,
    )
    base.update(overrides)
    return ApproachConfig(**base)


def test_disabled_or_aligned_returns_none() -> None:
    assert plan_approach((0.242, 0.0, -0.04), None) is None
    assert plan_approach(
        (0.242, 0.0, -0.04), _config(enabled=False)
    ) is None
    assert plan_approach((0.235, 0.003, -0.04), _config()) is None


def test_far_cube_pulses_forward() -> None:
    pulse = plan_approach((0.242, 0.0, -0.04), _config())
    assert pulse is not None
    assert pulse["vx"] == pytest.approx(1.0)
    assert pulse["vy"] == pytest.approx(0.0)
    assert pulse["forward_mm"] == pytest.approx(12.0, abs=0.1)
    assert pulse["duty"] == 10.0
    # 12 mm at 49 mm/s is 0.245 s, below the 0.25 s dead-band floor.
    assert pulse["duration_s"] == pytest.approx(0.25)
    assert pulse["nominal_travel_mm"] == pytest.approx(12.25, abs=0.1)


def test_near_cube_pulses_backward() -> None:
    pulse = plan_approach((0.200, 0.0, -0.04), _config())
    assert pulse is not None
    assert pulse["vx"] == pytest.approx(-1.0)
    assert pulse["forward_mm"] == pytest.approx(-30.0, abs=0.1)


def test_lateral_offset_pulses_left_and_caps_duration() -> None:
    pulse = plan_approach((0.240, 0.020, -0.04), _config())
    assert pulse is not None
    assert pulse["vx"] > 0.0 and pulse["vy"] > 0.0
    assert pulse["left_mm"] == pytest.approx(20.0, abs=0.1)
    long = plan_approach((0.400, 0.0, -0.04), _config())
    assert long is not None
    assert long["duration_s"] == pytest.approx(2.0)
    assert long["nominal_travel_mm"] == pytest.approx(98.0, abs=0.1)


def test_xy_only_direction() -> None:
    pulse = plan_approach((0.230, 0.030, -0.04), _config(tolerance_m=0.005))
    assert pulse is not None
    assert pulse["vx"] == pytest.approx(0.0, abs=1e-6)
    assert pulse["vy"] == pytest.approx(1.0)
