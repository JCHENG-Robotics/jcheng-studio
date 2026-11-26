#!/usr/bin/env python3
"""Jerk-limited joint-space motion profiles for the vendor servo bus.

The vendor ``FollowJointTrajectory`` controller forwards **one** servo command
per trajectory point, and each command carries a duration. The bus servo then
interpolates to the commanded pulse over that duration and holds. A single
point with a long duration is therefore one constant-velocity move with an
instantaneous velocity step at the start and at the stop; that step is what
excites the arm (the current 6-9 s single-point moves are slow *and* shake).

This module turns a start pose and a target pose into a dense, synchronised
waypoint list whose scalar path parameter ``s(t)`` follows a trapezoid with
cosine (jerk-limited) ramps:

* ``s = 0`` at ``t = 0`` and ``s = 1`` at the end, exactly;
* every joint follows the same ``s(t)``, so the joint path is a straight line
  and all joints start and stop together;
* the peak joint velocity is ``vmax`` and the peak joint acceleration is
  ``amax`` (both in rad/s and rad/s^2 of the fastest joint).

Pure Python (no ROS) so the laptop can unit-test it.
"""

import math

#: Smallest segment the vendor controller accepts (``ServoManager`` clamps a
#: duration below 20 ms up to 20 ms), so a shorter ``dt`` would silently
#: stretch the move.
MIN_SEGMENT_S = 0.025


def profile(distance, vmax, amax):
    """Return ``(v_peak, t_accel, t_cruise, total)`` for one move.

    The velocity ramps are half-cosine: the acceleration is continuous and the
    jerk is bounded. ``amax`` is the peak acceleration of the ramp.
    """
    distance = abs(float(distance))
    if distance <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    if vmax <= 0.0 or amax <= 0.0:
        raise ValueError('vmax and amax must be positive')
    t_accel = math.pi * vmax / (2.0 * amax)
    d_accel = vmax * t_accel / 2.0
    if 2.0 * d_accel >= distance:  # triangular: vmax is not reached
        v_peak = math.sqrt(2.0 * amax * distance / math.pi)
        t_accel = math.pi * v_peak / (2.0 * amax)
        return v_peak, t_accel, 0.0, 2.0 * t_accel
    t_cruise = (distance - 2.0 * d_accel) / vmax
    return vmax, t_accel, t_cruise, 2.0 * t_accel + t_cruise


def _ramp_distance(t, v_peak, t_accel):
    """Distance covered by a half-cosine velocity ramp from rest."""
    if t <= 0.0:
        return 0.0
    if t >= t_accel:
        return v_peak * t_accel / 2.0
    return v_peak * (t / 2.0
                     - t_accel / (2.0 * math.pi)
                     * math.sin(math.pi * t / t_accel))


def path_parameter(t, v_peak, t_accel, t_cruise, total, distance):
    """Return ``s`` in ``[0, distance]`` for time ``t`` of the profile."""
    if t <= 0.0:
        return 0.0
    if t >= total:
        return distance
    if t <= t_accel:
        return _ramp_distance(t, v_peak, t_accel)
    if t <= t_accel + t_cruise:
        return _ramp_distance(t_accel, v_peak, t_accel) + v_peak * (
            t - t_accel)
    return distance - _ramp_distance(total - t, v_peak, t_accel)


def plan_move(start, target, vmax, amax, dt=0.05):
    """Plan a synchronised S-curve move from ``start`` to ``target``.

    ``start`` and ``target`` are equal-length joint vectors in radians.
    Returns ``(times, waypoints, info)`` where ``times`` are the segment end
    times from the first point (``times[0] == 0``) and ``info`` reports the
    profile parameters and the peak velocity/acceleration per joint.

    A zero displacement is represented as one ``dt``-long hold segment at the
    target, not a zero-duration point: the gateway rejects a segment below
    25 ms, and the target equals the start pose.
    """
    start = [float(value) for value in start]
    target = [float(value) for value in target]
    if len(start) != len(target):
        raise ValueError('start and target need the same length')
    if dt < MIN_SEGMENT_S:
        raise ValueError('dt must be >= {:.3f} s'.format(MIN_SEGMENT_S))
    delta = [b - a for a, b in zip(start, target)]
    span = max(abs(value) for value in delta) if delta else 0.0
    if span <= 0.0:
        return [0.0, dt], [list(start), list(target)], {
            'v_peak': 0.0, 't_accel': 0.0, 't_cruise': 0.0, 'total': dt,
            'span': 0.0, 'peak_velocity': [0.0] * len(start),
            'peak_acceleration': [0.0] * len(start), 'segments': 1,
        }

    v_peak, t_accel, t_cruise, total = profile(span, vmax, amax)
    count = max(1, int(math.ceil(total / dt)))
    times = [total * index / count for index in range(count + 1)]
    times[-1] = total
    waypoints = []
    for t in times:
        s = path_parameter(t, v_peak, t_accel, t_cruise, total, span)
        waypoints.append([a + s / span * d for a, d in zip(start, delta)])
    # The commanded endpoints must be exact: the target is what the servo has
    # to reach, and the first point is the pose the profile started from.
    waypoints[0] = list(start)
    waypoints[-1] = list(target)
    # Peak per-joint kinematics: the profile is uniform in s, so each joint's
    # peak scales with its own displacement.
    scale = [abs(d) / span for d in delta]
    info = {
        'v_peak': v_peak,
        't_accel': t_accel,
        't_cruise': t_cruise,
        'total': total,
        'span': span,
        'peak_velocity': [v_peak * value for value in scale],
        'peak_acceleration': [amax * value for value in scale],
        'segments': count,
    }
    return times, waypoints, info


def split_move(start, target, keep):
    """Return the first ``keep`` fraction of a move, the rest unchanged."""
    return ([a + (b - a) * keep for a, b in zip(start, target)], target)
