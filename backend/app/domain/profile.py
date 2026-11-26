"""Jerk-limited joint-space profile for the G8 fast task path (pure).

The vendor controller forwards **one bus-servo command per trajectory point**
with that segment's duration, and the servo interpolates linearly. A single
long point is therefore one constant-velocity move with an instantaneous
velocity step at the start and stop: slow *and* shaky. A dense waypoint list
whose scalar path parameter follows a trapezoid with half-cosine ramps turns
the same end-to-end move into a real S-curve.

This is the pure port of ``tools/phase2/motion_profile.py`` (hardware-verified
on 2026-11-20 at 4.0 rad/s / 20 rad/s^2). The backend uses it to build the
concrete waypoints; the gateway validates every segment again before anything
reaches the servo bus.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# The vendor clamps a segment shorter than 20 ms up to 20 ms, which would
# silently stretch the move; 25 ms is the measured safe floor.
MIN_SEGMENT_S = 0.025
MAX_SEGMENT_S = 0.5


@dataclass(frozen=True)
class ProfileParams:
    """Peak joint velocity/acceleration and the waypoint spacing."""

    vmax_rad_s: float
    amax_rad_s2: float
    dt_s: float = 0.05

    def to_public_dict(self) -> dict[str, float]:
        return {
            "vmax_rad_s": self.vmax_rad_s,
            "amax_rad_s2": self.amax_rad_s2,
            "dt_s": self.dt_s,
        }


@dataclass(frozen=True)
class ProfilePlan:
    """Concrete waypoints: one segment per entry, endpoints exact."""

    durations_s: list[float]
    points: list[dict[str, float]]
    total_s: float
    segments: int
    v_peak: float
    peak_velocity_rad_s: dict[str, float]
    peak_acceleration_rad_s2: dict[str, float]

    @property
    def target(self) -> dict[str, float]:
        return dict(self.points[-1]) if self.points else {}

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "total_s": round(self.total_s, 6),
            "segments": self.segments,
            "v_peak_rad_s": round(self.v_peak, 6),
            "peak_velocity_rad_s": {
                name: round(value, 6)
                for name, value in self.peak_velocity_rad_s.items()
            },
            "peak_acceleration_rad_s2": {
                name: round(value, 6)
                for name, value in self.peak_acceleration_rad_s2.items()
            },
        }


def validate_params(params: ProfileParams) -> None:
    """Reject a profile that cannot be executed safely or faithfully."""
    if not math.isfinite(params.vmax_rad_s) or params.vmax_rad_s <= 0:
        raise ValueError("vmax_rad_s must be positive")
    if not math.isfinite(params.amax_rad_s2) or params.amax_rad_s2 <= 0:
        raise ValueError("amax_rad_s2 must be positive")
    if not MIN_SEGMENT_S <= params.dt_s <= MAX_SEGMENT_S:
        raise ValueError(
            f"dt_s must be within [{MIN_SEGMENT_S}, {MAX_SEGMENT_S}] s"
        )


def _profile(distance: float, vmax: float, amax: float) -> tuple[float, float, float, float]:
    """Return ``(v_peak, t_accel, t_cruise, total)`` for one move."""
    distance = abs(distance)
    if distance <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    t_accel = math.pi * vmax / (2.0 * amax)
    d_accel = vmax * t_accel / 2.0
    if 2.0 * d_accel >= distance:  # triangular: vmax is not reached
        v_peak = math.sqrt(2.0 * amax * distance / math.pi)
        t_accel = math.pi * v_peak / (2.0 * amax)
        return v_peak, t_accel, 0.0, 2.0 * t_accel
    t_cruise = (distance - 2.0 * d_accel) / vmax
    return vmax, t_accel, t_cruise, 2.0 * t_accel + t_cruise


def _ramp_distance(t: float, v_peak: float, t_accel: float) -> float:
    """Distance covered by a half-cosine velocity ramp from rest."""
    if t <= 0.0:
        return 0.0
    if t >= t_accel:
        return v_peak * t_accel / 2.0
    return v_peak * (
        t / 2.0 - t_accel / (2.0 * math.pi) * math.sin(math.pi * t / t_accel)
    )


def _path_parameter(
    t: float,
    v_peak: float,
    t_accel: float,
    t_cruise: float,
    total: float,
    distance: float,
) -> float:
    if t <= 0.0:
        return 0.0
    if t >= total:
        return distance
    if t <= t_accel:
        return _ramp_distance(t, v_peak, t_accel)
    if t <= t_accel + t_cruise:
        return _ramp_distance(t_accel, v_peak, t_accel) + v_peak * (t - t_accel)
    return distance - _ramp_distance(total - t, v_peak, t_accel)


def plan_move(
    start: dict[str, float],
    target: dict[str, float],
    params: ProfileParams,
) -> ProfilePlan:
    """Plan a synchronised S-curve from ``start`` to ``target``.

    Both poses must carry the same joint names. The returned points are the
    **segment end points** (the start pose is not repeated): entry ``i`` is the
    pose after ``durations_s[i]`` seconds, and the last entry is exactly
    ``target``.

    A zero displacement is represented as one ``dt_s``-long hold segment at
    the target, not an empty plan: the gateway rejects a zero-duration
    segment, and the target equals the start pose. Callers that do not need a
    hold may skip the command when the displacement is zero.
    """
    validate_params(params)
    names = sorted(start)
    if set(start) != set(target):
        raise ValueError("start and target need the same joints")
    start_values = [float(start[name]) for name in names]
    target_values = [float(target[name]) for name in names]
    delta = [b - a for a, b in zip(start_values, target_values)]
    span = max((abs(value) for value in delta), default=0.0)
    if span <= 0.0:
        return ProfilePlan(
            durations_s=[params.dt_s],
            points=[dict(zip(names, target_values))],
            total_s=params.dt_s,
            segments=1,
            v_peak=0.0,
            peak_velocity_rad_s={name: 0.0 for name in names},
            peak_acceleration_rad_s2={name: 0.0 for name in names},
        )

    v_peak, t_accel, t_cruise, total = _profile(
        span, params.vmax_rad_s, params.amax_rad_s2
    )
    count = max(1, int(math.ceil(total / params.dt_s)))
    times = [total * index / count for index in range(count + 1)]
    times[-1] = total

    points: list[dict[str, float]] = []
    for t in times[1:]:  # skip the start: it is the current pose
        s = _path_parameter(t, v_peak, t_accel, t_cruise, total, span)
        points.append(
            {
                name: (
                    start_values[index]
                    + s / span * delta[index]
                )
                for index, name in enumerate(names)
            }
        )
    points[-1] = dict(zip(names, target_values))

    durations = [
        times[index + 1] - times[index] for index in range(len(times) - 1)
    ]
    scale = {name: abs(delta[index]) / span for index, name in enumerate(names)}
    return ProfilePlan(
        durations_s=durations,
        points=points,
        total_s=total,
        segments=count,
        v_peak=v_peak,
        peak_velocity_rad_s={
            name: v_peak * scale[name] for name in names
        },
        peak_acceleration_rad_s2={
            name: params.amax_rad_s2 * scale[name] for name in names
        },
    )


def segment_velocities(
    start: dict[str, float],
    points: list[dict[str, float]],
    durations: list[float],
) -> dict[str, list[float]]:
    """Per-joint segment velocities (rad/s) from ``start`` to each point."""
    if not points or len(points) != len(durations):
        raise ValueError("points and durations must be non-empty and equal")
    names = sorted(start)
    velocities: dict[str, list[float]] = {name: [] for name in names}
    previous = {name: float(start[name]) for name in names}
    for point, duration in zip(points, durations):
        if duration <= 0:
            raise ValueError("segment durations must be positive")
        for name in names:
            value = float(point[name])
            velocities[name].append(abs(value - previous[name]) / duration)
        previous = {name: float(point[name]) for name in names}
    return velocities
