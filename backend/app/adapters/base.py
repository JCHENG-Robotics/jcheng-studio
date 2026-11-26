"""Robot adapter boundary.

The backend only talks to an adapter. Mock and hardware are separate
implementations; the hardware adapter has no motion write path in G1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..domain.enums import CommandStage, Mode, SourceLabel
from ..domain.models import (
    BatteryState,
    CameraStatus,
    Capabilities,
    ConnectionReport,
    RobotState,
    StopResult,
)


class HardwareMotionDisabled(RuntimeError):
    """Raised by the hardware adapter when motion is requested in G1."""


class AdapterError(RuntimeError):
    """Structured adapter failure."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class DispatchResult:
    status: CommandStage
    error_code: str | None
    detail: str
    details: dict[str, Any] = field(default_factory=dict)


class RobotAdapter(ABC):
    mode: Mode
    source_label: SourceLabel
    capabilities: Capabilities

    @abstractmethod
    def tick(self) -> None:
        """Advance simulated time or poll the hardware boundary."""

    @abstractmethod
    def snapshot(self) -> RobotState:
        """Return the latest state, clearly labelled with its truth source."""

    @abstractmethod
    def connection_report(self) -> ConnectionReport:
        """Return four-segment connection evidence."""

    @abstractmethod
    def battery(self) -> BatteryState:
        """Return the latest battery sample, or an explicit unavailable state."""

    @abstractmethod
    def camera_status(self) -> CameraStatus:
        """Return camera health, or an explicit unavailable state."""

    @abstractmethod
    def camera_snapshot(self) -> tuple[bytes, str] | None:
        """Return (image bytes, media type), or None when no frame is available."""

    def aux_camera_status(self) -> CameraStatus:
        """Return the off-arm UVC camera health; unavailable by default."""
        return CameraStatus(
            available=False,
            truth=Truth.UNKNOWN,
            validity=Validity.UNAVAILABLE,
            source="adapter",
            topic="uvc:disabled",
            width=None,
            height=None,
            encoding=None,
            fps=None,
            last_frame_at=None,
            sample_age_ms=None,
            frame_count=0,
            notes=["aux camera is not supported by this adapter"],
        )

    def aux_camera_snapshot(self) -> tuple[bytes, str] | None:
        """Return the off-arm UVC frame, or None when unavailable."""
        return None

    def tf_snapshot(self) -> dict[str, Any] | None:
        """Return a read-only TF snapshot when the adapter supports one."""
        return None

    def chassis_pulse(self, body: dict[str, Any]) -> dict[str, Any] | None:
        """Send one bounded chassis pulse; hardware only."""
        return None

    def pointcloud(self) -> dict[str, Any] | None:
        """Return the latest registered point cloud, or None when unavailable.

        The hardware adapter decodes the gateway's float32 XYZ payload; the
        mock adapter returns a synthetic cloud only when a test supplies one.
        The service fails closed when this is None.
        """
        return None

    def last_actual(self) -> dict[str, Any] | None:
        """Return the last bus-servo read (measured_on_demand), or None.

        The task locate uses this for the hand-eye FK: the vendor command cache
        at the view can differ by ~0.03 rad, which is ~9 mm at 0.3 m. The
        service only uses it when the arm is at the view and not executing.
        """
        return None

    @abstractmethod
    def dispatch(
        self,
        targets: list[dict[str, Any]],
        command_id: str,
        *,
        fast: bool = False,
    ) -> DispatchResult:
        """Send a validated target set. Hardware must reject in G1.

        ``fast`` marks the G8 task's measured gripper timings; the gateway
        uses the ``safety.profile`` block instead of the slow path's 1.0 s
        floor and 100 raw/s cap. It must be forwarded, not inferred.
        """

    def dispatch_profile(
        self,
        targets: list[dict[str, Any]],
        points: list[dict[str, Any]],
        command_id: str,
    ) -> DispatchResult:
        """Send a dense S-curve profile.

        ``points`` carries one ``{duration_s, targets}`` entry per segment.
        The default implementation degrades to one endpoint command so mock
        adapters stay simple; the hardware adapter forwards the waypoints to
        the gateway, which validates every segment.
        """
        return self.dispatch(targets, command_id)

    @abstractmethod
    def stop(self, command_id: str) -> StopResult:
        """Request a stop. The result is evidence-based, never an emergency stop."""

    @abstractmethod
    def drain_events(self) -> list[dict[str, Any]]:
        """Return adapter events (observed/completed/unknown) for the audit log."""

    @abstractmethod
    def reset(self) -> None:
        """Reset mock faults/motion. Hardware must not implement side effects."""
