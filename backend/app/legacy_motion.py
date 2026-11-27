"""Deprecated legacy motion policy.

The frozen pre-ecosystem HTTP gateway is a transitional transport. This module
is the only place in the backend that names its motion paths, so the default
runtime never references them. The paths are default-disabled and reachable
only through the double switch ``ARMPI_MODE=legacy`` **and**
``ARMPI_ENABLE_LEGACY_MOTION=1`` (Settings.legacy_motion_enabled).

This stage is *default disable + isolation*, not deletion: the classes and
endpoints remain for the operator's migration window, but they carry no motion
authority and every entry point fails with HTTP 410 Gone unless the operator
explicitly re-enables the deprecated path.

**Operator-only, not system-authorized.** The frozen gateway has no lease
endpoint, so the double-switch mode cannot validate a System lease: any
non-empty ``lease_id`` is accepted as an opaque operator reference and only
recorded for audit. An empty or whitespace-only lease is rejected locally with
HTTP 400 before dispatch. This mode is not an authorization or safety
guarantee, must not be used for normal robot operation, and must not be
described as safe. It is scheduled for removal; the residual boundary is
tracked by a formal boundary waiver (``B4 studio legacy_motion.py``) with an
expiry and a named removal owner.

The single authoritative intent and stop paths are:

- ``POST /api/session/intent/joint`` and ``.../gripper`` with a System-issued
  ``lease_id`` (requires ``ARMPI_MODE=hardware`` and ``ARMPI_SYSTEM_INTENT=1``);
- ``POST /api/teleop/stop`` which proxies the system ``POST /v1/motion/stop``.

See ``docs/INTEGRATION.md`` (legacy motion deprecation) for the migration.
"""

from __future__ import annotations

from .errors import ServiceError

# Frozen pre-ecosystem gateway paths. Referenced only by the deprecated
# adapter branch; a static test asserts no default module mentions them.
LEGACY_DISPATCH_PATH = "/motion/dispatch"
LEGACY_STOP_PATH = "/motion/stop"
LEGACY_CHASSIS_PULSE_PATH = "/chassis/pulse"

LEGACY_MOTION_DISABLED = "LEGACY_MOTION_DISABLED"

MIGRATION_HINT = (
    "Use POST /api/session/intent/joint or /api/session/intent/gripper with a "
    "System-issued lease_id (ARMPI_MODE=hardware, ARMPI_SYSTEM_INTENT=1), and "
    "POST /api/teleop/stop for the single system stop. The Studio never holds "
    "a motion lease."
)

ENABLE_HINT = (
    "ARMPI_MODE=legacy and ARMPI_ENABLE_LEGACY_MOTION=1 "
    "(deprecated, isolated operator switch)"
)


def legacy_motion_error(operation: str) -> ServiceError:
    """Build the 410 Gone response for a legacy motion entry point."""
    return ServiceError(
        LEGACY_MOTION_DISABLED,
        f"legacy motion entry point {operation} is disabled by default",
        status_code=410,
        details={
            "operation": operation,
            "migration": MIGRATION_HINT,
            "enable": ENABLE_HINT,
            "policy": "docs/INTEGRATION.md#legacy-motion-deprecation",
        },
    )
