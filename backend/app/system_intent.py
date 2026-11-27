"""System-intent policy: hardware-only, opt-in, never a local authority.

The Studio forwards one operator intent to ``jcheng-system``
(``/v1/motion/validate`` + ``/v1/motion/execute``) with a lease the system
issued. It never acquires, checks, renews or holds a motion lease. The path is
therefore refused outside hardware mode and refused unless the operator opted
in with ``ARMPI_SYSTEM_INTENT=1``.

Mock and legacy mode are refused too: neither may reach a real system gateway.
"""

from __future__ import annotations

from .domain.enums import Mode
from .errors import ServiceError


def require_system_intent(settings, operation: str) -> None:
    """Raise 409/403 unless system intent is allowed in this process."""
    if settings.mode is not Mode.HARDWARE:
        raise ServiceError(
            "SYSTEM_INTENT_REQUIRES_HARDWARE",
            f"{operation} requires ARMPI_MODE=hardware; "
            f"{settings.mode.value} mode cannot forward system intent",
            status_code=409,
            details={"mode": settings.mode.value},
        )
    if not settings.system_intent_enabled:
        raise ServiceError(
            "SYSTEM_INTENT_DISABLED",
            f"{operation} requires ARMPI_SYSTEM_INTENT=1; the Studio never "
            "acquires a lease or moves the robot by default",
            status_code=403,
        )
