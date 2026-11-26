"""Deep grasp-family j1/j4 fit for G8 task takeover.

A port of ``tools/phase2/grasp_joint_fit.py`` that uses the backend's audited
URDF forward kinematics instead of PyKDL. The family is the one every
validated 2026-11-19/21 floor grasp used: joint2 and joint3 at their adopted
low bounds, joint5 = 0 (the user's standing rule), joint1 on the target
azimuth, and joint4 setting the reach. The z coordinate is not fitted; the
pads span the top face.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

DEEP_J2 = -0.6618
# The Studio's adopted low bound is -1.8011798; the phase-3 tool uses -1.8012,
# which is 2e-5 outside it. Use the limit-bound value so validation accepts it.
DEEP_J3 = -1.80117
DEFAULT_J5 = 0.0
J4_LIMITS = (-1.59174, 1.59174)


def _numerical_jacobian(
    function: Callable[[list[float]], np.ndarray],
    joints: list[float],
    step: float = 1e-6,
) -> np.ndarray:
    base = function(joints)
    jacobian = np.empty((2, 2))
    for index, joint in enumerate((0, 3)):
        delta = list(joints)
        delta[joint] += step
        jacobian[:, index] = (function(delta) - base) / step
    return jacobian


def fit(
    target,
    tcp: Callable[[list[float]], np.ndarray],
    j2: float = DEEP_J2,
    j3: float = DEEP_J3,
    j5: float = DEFAULT_J5,
    j4_limits: tuple[float, float] = J4_LIMITS,
    max_iterations: int = 50,
) -> dict:
    """Fit j1/j4 so the TCP xy lands on *target*.

    Returns the five joints, the TCP, the residual (mm), the xy error (mm) and
    the solver status, matching the CLI tool's JSON.
    """
    target = np.asarray(target, dtype=np.float64).reshape(3)
    joints = [0.0, float(j2), float(j3), -0.35, float(j5)]

    def tcp_xy(values):
        return np.asarray(tcp(values), dtype=np.float64)[:2]

    cost = float(np.linalg.norm(tcp_xy(joints) - target[:2]))
    damping = 1e-3
    for _ in range(max_iterations):
        residual = tcp_xy(joints) - target[:2]
        jacobian = _numerical_jacobian(tcp_xy, joints)
        normal = jacobian.T @ jacobian
        gradient = jacobian.T @ residual
        proposal = np.linalg.solve(
            normal + damping * (np.diag(np.diag(normal)) + 1e-12 * np.eye(2)),
            -gradient,
        )
        candidate = list(joints)
        candidate[0] += float(proposal[0])
        candidate[3] = float(
            np.clip(candidate[3] + proposal[1], *j4_limits)
        )
        candidate_cost = float(
            np.linalg.norm(tcp_xy(candidate) - target[:2])
        )
        if candidate_cost < cost:
            improvement = cost - candidate_cost
            joints = candidate
            cost = candidate_cost
            damping = max(damping * 0.3, 1e-12)
            if improvement < 1e-12:
                break
        else:
            damping *= 10.0
            if damping > 1e12:
                break
    tcp_position = np.asarray(tcp(joints), dtype=np.float64)
    return {
        "joints": [float(value) for value in joints],
        "tcp": [float(value) for value in tcp_position],
        "target": [float(value) for value in target],
        "residual_mm": [
            float(value * 1000.0) for value in (tcp_position - target)
        ],
        "xy_error_mm": float(cost * 1000.0),
        "reach_limited": bool(
            joints[3] <= j4_limits[0] + 1e-6 and cost > 0.002
        ),
    }


def fit_with_j4(
    target,
    tcp: Callable[[list[float]], np.ndarray],
    j2: float,
    j3: float,
    j4: float,
    j5: float = 0.0,
    max_iterations: int = 30,
) -> dict:
    """Hold joint4 at the catalog-measured depth and solve joint1 on azimuth.

    The 2026-11-23 object-form poses pair a measured wrist depth with joint1
    from the target azimuth. Reusing them in the task plan means joint4 must
    not be re-fitted: the catalog joint4 is the depth whose pads reached the
    object, and re-solving it for the TCP xy would put the pads back above the
    top face. Only joint1 is free here.

    The TCP xy residual is reported, not rejected: the contact point is the
    pads, which sit in front of the nominal TCP, so the TCP is deliberately
    not placed on the target.
    """
    target = np.asarray(target, dtype=np.float64).reshape(3)
    joints = [0.0, float(j2), float(j3), float(j4), float(j5)]
    target_angle = math.atan2(target[1], target[0])
    for _ in range(max_iterations):
        position = np.asarray(tcp(joints), dtype=np.float64)
        angle = math.atan2(position[1], position[0])
        probe = list(joints)
        probe[0] += 1e-6
        probe_position = np.asarray(tcp(probe), dtype=np.float64)
        probe_angle = math.atan2(probe_position[1], probe_position[0])
        derivative = (probe_angle - angle) / 1e-6
        if not math.isfinite(derivative) or abs(derivative) < 1e-9:
            break
        error = math.atan2(
            math.sin(target_angle - angle), math.cos(target_angle - angle)
        )
        delta = error / derivative
        joints[0] += float(np.clip(delta, -0.5, 0.5))
        if abs(delta) < 1e-9:
            break
    tcp_position = np.asarray(tcp(joints), dtype=np.float64)
    return {
        "joints": [float(value) for value in joints],
        "tcp": [float(value) for value in tcp_position],
        "target": [float(value) for value in target],
        "residual_mm": [
            float(value * 1000.0) for value in (tcp_position - target)
        ],
        "xy_error_mm": float(
            np.linalg.norm(tcp_position[:2] - target[:2]) * 1000.0
        ),
        "reach_limited": False,
        "j4_source": "form",
    }


def validate_target(
    target,
    result: dict,
    max_xy_error_mm: float,
    reach: tuple[float, float] | None = (0.10, 0.30),
    *,
    radius_m: float | None = None,
) -> list[str]:
    """Return a list of refusal reasons; empty means the fit is usable.

    ``radius_m`` overrides the target radius for a form whose joint4 is fixed:
    the measured point is the object's position, while the catalog's +27 mm
    aim is only a TCP aiming offset, so gating the corrected target radius
    would falsely reject a valid object at the edge of the band. ``reach=None``
    skips the band entirely for a form already gated by its measured base
    window.
    """
    target = np.asarray(target, dtype=np.float64)
    reasons: list[str] = []
    if not np.all(np.isfinite(target)):
        return ["target is not finite"]
    if reach is not None:
        radius = (
            float(radius_m)
            if radius_m is not None
            else float(np.linalg.norm(target[:2]))
        )
        if not reach[0] <= radius <= reach[1]:
            reasons.append(
                f"target radius {radius:.3f} m is outside {reach[0]}-{reach[1]} m"
            )
    if result["xy_error_mm"] > max_xy_error_mm:
        reasons.append(
            f"joint fit xy error {result['xy_error_mm']:.2f} mm exceeds "
            f"{max_xy_error_mm:.2f} mm"
        )
    if result.get("reach_limited"):
        reasons.append("joint4 is at its low limit; target out of reach")
    if any(not math.isfinite(value) for value in result["joints"]):
        reasons.append("fit returned a non-finite joint")
    return reasons
