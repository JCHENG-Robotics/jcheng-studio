"""G8.1 chassis approach planning (pure).

The base has no odometry: the approach is a bounded open-loop pulse whose
effect is measured again by the next locate. One pulse aims the cube at
``target_point_m`` in ``base_link`` (the middle of the verified direct-grasp
band, on the tool-down axis), so the reach and the constant correction both
stay inside their evidence envelopes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .takeover import ApproachConfig


@dataclass(frozen=True)
class ApproachPulse:
    vx: float
    vy: float
    duty: float
    duration_s: float
    nominal_travel_mm: float
    travel_m: float
    forward_mm: float
    left_mm: float
    point_m: tuple[float, float, float]
    target_point_m: tuple[float, float]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "kind": "chassis_pulse",
            "vx": self.vx,
            "vy": self.vy,
            "duty": self.duty,
            "duration_s": self.duration_s,
            "nominal_travel_mm": self.nominal_travel_mm,
            "travel_m": self.travel_m,
            "forward_mm": self.forward_mm,
            "left_mm": self.left_mm,
            "point_m": list(self.point_m),
            "target_point_m": list(self.target_point_m),
        }


def plan_approach(
    point: Any,
    config: ApproachConfig | None,
) -> dict[str, Any] | None:
    """Return the next pulse toward the aim point, or ``None`` when aligned."""
    if config is None or not config.enabled:
        return None
    x = float(point[0])
    y = float(point[1])
    z = float(point[2]) if len(point) > 2 else 0.0
    target_x, target_y = config.target_point_m
    dx = x - target_x
    dy = y - target_y
    travel = math.hypot(dx, dy)
    if travel <= config.tolerance_m:
        return None
    duration = travel * 1000.0 / (
        config.duty * config.speed_mm_s_per_duty
    )
    duration = max(config.min_duration_s, min(config.max_duration_s, duration))
    nominal = config.duty * config.speed_mm_s_per_duty * duration
    pulse = ApproachPulse(
        vx=round(dx / travel, 6),
        vy=round(dy / travel, 6),
        duty=config.duty,
        duration_s=round(duration, 3),
        nominal_travel_mm=round(nominal, 1),
        travel_m=round(travel, 4),
        forward_mm=round(dx * 1000.0, 1),
        left_mm=round(dy * 1000.0, 1),
        point_m=(x, y, z),
        target_point_m=(target_x, target_y),
    )
    return pulse.to_public_dict()
