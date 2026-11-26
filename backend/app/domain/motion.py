"""Effective motion limits for the supervised and pose write paths.

The mapping file keeps the *verified* limits that both mock and read-only
hardware use. Three write scopes exist:

``step``
    G6 supervised commissioning: one joint, a 0.10 rad step cap and a 1.0 s
    duration floor. This is the default and the only scope an unverified
    mapping may use.
``pose``
    G7 takeover: one whole five-joint pose (or one gripper command), a
    velocity cap, and the FK workspace/table gate. It requires a verified
    mapping, the fixed ``TAKEOVER_ACK`` phrase and a gateway that advertises
    the matching ``pose`` motion mode.
``standard``
    The mapping's own limits for mock/preview work; no hardware dispatch.

This module is the single source for those effective values so the service,
the safety supervisor and the hardware adapter cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mapping import MappingConfig
from .takeover import TakeoverConfig

# The G6 acknowledgement is deliberately a fixed phrase and not a boolean. It
# is read from the environment so enabling motion needs an action in the launch
# command, not just a value in a UI form.
SUPERVISED_MOTION_ACK = "I_HAVE_SUPPORT_AND_A_CLEAR_WORKSPACE"

# Supervised commissioning never exceeds these values, even if the mapping's
# safety block is more permissive. 0.10 rad is about 2.4 degrees per command;
# a 1.0 s floor keeps the vendor's pulse interpolation visibly slow.
SUPERVISED_MAX_STEP_RAD = 0.10
SUPERVISED_MIN_DURATION_S = 1.0


@dataclass(frozen=True)
class EffectiveMotionLimits:
    """Limits used by validation, the safety gate and the adapter."""

    max_step_rad: float
    min_duration_s: float
    max_duration_s: float
    single_joint_only: bool
    supervised: bool
    scope: str = "standard"
    max_velocity_rad_s: float | None = None
    gripper_enabled: bool = False

    @property
    def mode_label(self) -> str:
        if self.scope == "pose":
            return "pose_takeover"
        return "supervised" if self.supervised else "standard"

    @property
    def takeover(self) -> bool:
        return self.scope == "pose"


def effective_motion_limits(
    mapping: MappingConfig,
    *,
    supervised: bool,
    takeover: TakeoverConfig | None = None,
) -> EffectiveMotionLimits:
    """Return the active step/duration/velocity limits.

    ``supervised`` is true only when the operator supplied the G6 fixed
    acknowledgement. ``takeover`` is the loaded G7 contract and is supplied
    only when the startup checks accepted the pose scope. Neither must ever be
    derived from a UI checkbox.
    """
    if takeover is not None:
        # A pose command spans a whole configuration. The per-command step cap
        # is therefore the widest joint span; the real bound is the velocity
        # cap plus the verified joint limits.
        widest = max(
            joint.max_rad - joint.min_rad for joint in mapping.joints.values()
        )
        return EffectiveMotionLimits(
            max_step_rad=widest,
            min_duration_s=max(mapping.safety.min_duration_s, takeover.min_duration_s),
            max_duration_s=min(mapping.safety.max_duration_s, takeover.max_duration_s),
            single_joint_only=False,
            supervised=False,
            scope="pose",
            max_velocity_rad_s=takeover.max_velocity_rad_s,
            gripper_enabled=True,
        )
    if supervised:
        # Align the cap down to a whole servo pulse so a target accepted here
        # cannot map to one more pulse than the gateway allows. The gateway
        # uses floor(step / radians_per_tick); a radian cap that sits between
        # two pulses would otherwise validate then fail at dispatch.
        step_pulses = int(SUPERVISED_MAX_STEP_RAD / mapping.radians_per_tick)
        aligned_step = step_pulses * mapping.radians_per_tick
        return EffectiveMotionLimits(
            max_step_rad=min(mapping.safety.max_step_rad, aligned_step),
            min_duration_s=max(
                mapping.safety.min_duration_s, SUPERVISED_MIN_DURATION_S
            ),
            max_duration_s=mapping.safety.max_duration_s,
            single_joint_only=True,
            supervised=True,
            scope="step",
        )
    return EffectiveMotionLimits(
        max_step_rad=mapping.safety.max_step_rad,
        min_duration_s=mapping.safety.min_duration_s,
        max_duration_s=mapping.safety.max_duration_s,
        single_joint_only=False,
        supervised=False,
        scope="standard",
    )
