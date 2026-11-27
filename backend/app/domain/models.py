"""Domain data models.

These are plain dataclasses so the safety core and mock adapter can be tested
without FastAPI, pydantic, ROS, or a serial port.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .enums import (
    BatteryStatus,
    CommandStage,
    ConnectionState,
    FaultLevel,
    Mode,
    Severity,
    SourceLabel,
    StopState,
    Truth,
    Validity,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def jsonable(value: Any) -> Any:
    """Convert dataclasses/enums to JSON-safe primitives."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass
class JointState:
    name: str
    servo_id: int
    target_rad: float | None
    target_raw: int | None
    position_rad: float | None
    raw_position: int | None
    truth: Truth
    validity: Validity
    sample_age_ms: int | None
    source: str
    limit_min_rad: float
    limit_max_rad: float
    limit_verified: bool


@dataclass
class Capabilities:
    hardware_motion: bool = False
    hardware_read: bool = False
    mock_faults: bool = False
    replay: bool = False
    min_duration_s: float = 0.5
    max_duration_s: float = 10.0
    max_step_rad: float = 0.35
    supervised_motion: bool = False
    single_joint_only: bool = False
    motion_mode: str = "read-only"
    motion_scope: str = "read-only"  # read-only | step | pose | standard
    max_velocity_rad_s: float | None = None
    gripper_control: bool = False
    pose_gate: bool = False


@dataclass
class GripperState:
    """Read-only gripper telemetry plus the measured command range.

    The vendor maps ``r_joint`` around init pulse 700: open raw 170 measures
    register 167, and an empty close stalls at register 640 while the command
    is 700. Completion is therefore timed, not positional.
    """

    joint: str
    servo_id: int
    available: bool
    target_rad: float | None
    target_raw: int | None
    position_rad: float | None
    raw_position: int | None
    truth: Truth
    validity: Validity
    sample_age_ms: int | None
    source: str
    min_rad: float
    max_rad: float
    open_raw: int
    close_raw: int
    measured_min_raw: int
    measured_max_raw: int
    completion: str = "timed"
    notes: list[str] = field(default_factory=list)


@dataclass
class Fault:
    fault_id: str
    level: FaultLevel
    code: str
    message: str
    source: str
    first_seen: str
    last_seen: str
    remediation: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SegmentStatus:
    name: str
    state: str
    detail: str
    evidence: str


@dataclass
class ConnectionReport:
    segments: list[SegmentStatus]
    serial_available: bool
    driver_online: bool
    gateway_online: bool
    feedback_fresh: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class SafetyCheck:
    check_id: str
    label: str
    passed: bool
    blocking: bool
    detail: str
    severity: Severity = Severity.INFO


@dataclass
class SafetyReport:
    ready: bool
    checks: list[SafetyCheck]
    blockers: list[str]
    warnings: list[str]
    mode: Mode
    generated_at: str

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)


@dataclass
class MotionTarget:
    joint: str
    target_rad: float
    duration_s: float


@dataclass
class ValidationIssue:
    code: str
    message: str
    joint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreviewResult:
    command_id: str
    session_id: str
    valid: bool
    targets: list[dict[str, Any]]
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    preview_token: str | None
    expires_at: str | None
    mapping_version: str
    evaluated_at: str
    pose: dict[str, Any] | None = None


@dataclass
class CommandRecord:
    command_id: str
    session_id: str
    status: CommandStage
    targets: list[dict[str, Any]]
    created_at: str
    updated_at: str
    error_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    stage_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StopResult:
    state: StopState
    command_id: str
    detail: str
    evidence: str
    requested_at: str
    observed_at: str | None = None


@dataclass
class BatteryState:
    """Battery telemetry from the vendor battery topic.

    The vendor topic is a 2S pack voltage in millivolts. `available=False`
    means "no valid sample"; never fill a missing value with zero.
    """

    available: bool
    voltage_mv: int | None
    voltage_v: float | None
    percent_estimate: float | None
    cell_count: int
    status: BatteryStatus
    truth: Truth
    validity: Validity
    source: str
    topic: str
    received_at: str
    sample_age_ms: int | None
    notes: list[str] = field(default_factory=list)


@dataclass
class CameraStatus:
    """Read-only camera health for the Deptrum Aurora 930 RGB stream."""

    available: bool
    truth: Truth
    validity: Validity
    source: str
    topic: str
    width: int | None
    height: int | None
    encoding: str | None
    fps: float | None
    last_frame_at: str | None
    sample_age_ms: int | None
    frame_count: int
    notes: list[str] = field(default_factory=list)


@dataclass
class ControlLease:
    lease_id: str
    session_id: str
    acquired_at: str
    expires_at: str
    ttl_s: int
    expires_monotonic: float = 0.0


@dataclass
class Event:
    event_id: str
    command_id: str | None
    stage: str
    wall_time: str
    monotonic_ms: float
    source: str
    result: str
    error_code: str | None
    details: dict[str, Any]
    schema_version: int = 1


@dataclass
class RobotState:
    robot_id: str
    mode: Mode
    connection_state: ConnectionState
    source_label: SourceLabel
    control_owner: str | None
    received_at: str
    monotonic_ms: float
    ros_time_ns: int | None
    joints: list[JointState]
    faults: list[Fault]
    mapping_version: str
    limit_version: str
    mapping_verified: bool
    capabilities: Capabilities
    stop_state: StopState | None = None
    last_command_id: str | None = None
    gripper: "GripperState | None" = None


@dataclass
class Diagnostics:
    mode: Mode
    connection: ConnectionReport
    mapping: dict[str, Any]
    safety: SafetyReport
    capabilities: Capabilities
    known_defects: list[dict[str, str]]
    probe_available: bool
    environment: dict[str, Any]
    mock_faults: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Health:
    status: str
    mode: Mode
    version: str
    schema_version: int
    source_label: SourceLabel
    capabilities: Capabilities
    demo: bool = False
