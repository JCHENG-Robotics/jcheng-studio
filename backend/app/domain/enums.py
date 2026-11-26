"""Domain enumerations shared by the API, safety core, and adapters."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


class Mode(StrEnum):
    MOCK = "mock"
    HARDWARE = "hardware"


class SourceLabel(StrEnum):
    LIVE = "LIVE"
    SIMULATION = "SIMULATION"
    REPLAY = "REPLAY"


class ConnectionState(StrEnum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    READ_ONLY = "ReadOnly"
    READY = "Ready"
    EXECUTING = "Executing"
    DEGRADED = "Degraded"
    FAULT = "Fault"
    ESTOP_OR_POWER_CUT_REPORTED = "EStopOrPowerCutReported"
    REPLAY = "Replay"
    SIMULATION = "Simulation"


class CommandStage(StrEnum):
    REQUESTED = "Requested"
    VALIDATED = "Validated"
    DISPATCHED = "Dispatched"
    ACKNOWLEDGED = "Acknowledged"  # intentionally unreachable: the SDK has no ACK
    OBSERVED = "Observed"
    COMPLETED = "Completed"
    FAILED = "Failed"
    UNKNOWN = "Unknown"


class Truth(StrEnum):
    COMMANDED = "commanded"
    MEASURED = "measured"
    MEASURED_ON_DEMAND = "measured_on_demand"
    SIMULATED = "simulated"
    UNKNOWN = "unknown"


class Validity(StrEnum):
    VALID = "valid"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    FAULT = "fault"
    CRITICAL = "critical"


class FaultLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    FAULT = "fault"
    CRITICAL = "critical"


class StopState(StrEnum):
    STOP_REQUESTED = "StopRequested"
    STOP_DISPATCHED = "StopDispatched"
    STOP_OBSERVED = "StopObserved"
    STOP_UNKNOWN = "StopUnknown"


class BatteryStatus(StrEnum):
    OK = "ok"
    LOW = "low"
    CRITICAL = "critical"
    UNAVAILABLE = "unavailable"


class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    UNKNOWN_JOINT = "UNKNOWN_JOINT"
    GRIPPER_NOT_IN_G1 = "GRIPPER_NOT_IN_G1"
    NON_FINITE_TARGET = "NON_FINITE_TARGET"
    OUT_OF_LIMIT = "OUT_OF_LIMIT"
    STEP_EXCEEDED = "STEP_EXCEEDED"
    DURATION_RANGE = "DURATION_RANGE"
    DUPLICATE_JOINT = "DUPLICATE_JOINT"
    NO_TARGETS = "NO_TARGETS"
    SUPERVISED_SINGLE_JOINT = "SUPERVISED_SINGLE_JOINT"
    POSE_JOINT_SET = "POSE_JOINT_SET"
    VELOCITY_EXCEEDED = "VELOCITY_EXCEEDED"
    GRIPPER_RANGE = "GRIPPER_RANGE"
    WORKSPACE = "WORKSPACE"
    TABLE_CLEARANCE = "TABLE_CLEARANCE"
    LOW_POSE_YAW = "LOW_POSE_YAW"
    FK_UNAVAILABLE = "FK_UNAVAILABLE"
    POINTCLOUD_UNAVAILABLE = "POINTCLOUD_UNAVAILABLE"
    POINTCLOUD_STALE = "POINTCLOUD_STALE"
    MOTION_OUTCOME_UNKNOWN = "MOTION_OUTCOME_UNKNOWN"
    NO_LEASE = "NO_LEASE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_CONFLICT = "LEASE_CONFLICT"
    PREVIEW_INVALID = "PREVIEW_INVALID"
    PREVIEW_EXPIRED = "PREVIEW_EXPIRED"
    PREVIEW_PAYLOAD_MISMATCH = "PREVIEW_PAYLOAD_MISMATCH"
    PREVIEW_REUSED = "PREVIEW_REUSED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    HARDWARE_MOTION_DISABLED = "HARDWARE_MOTION_DISABLED"
    GATEWAY_UNAVAILABLE = "GATEWAY_UNAVAILABLE"
    DRIVER_OFFLINE = "DRIVER_OFFLINE"
    FEEDBACK_STALE = "FEEDBACK_STALE"
    ACTIVE_FAULT = "ACTIVE_FAULT"
    ALREADY_EXECUTING = "ALREADY_EXECUTING"
    STORAGE_WRITE_FAILED = "STORAGE_WRITE_FAILED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class FaultCode(StrEnum):
    MOCK_TIMEOUT = "MOCK_TIMEOUT"
    MOCK_DISCONNECT = "MOCK_DISCONNECT"
    MOCK_EMPTY_FEEDBACK = "MOCK_EMPTY_FEEDBACK"
    MOCK_NAN_FEEDBACK = "MOCK_NAN_FEEDBACK"
    MOCK_MISSING_SERVO = "MOCK_MISSING_SERVO"
    MOCK_DRIVER_EXIT = "MOCK_DRIVER_EXIT"
    STORAGE_WRITE_FAILED = "STORAGE_WRITE_FAILED"
    HARDWARE_MOTION_DISABLED = "HARDWARE_MOTION_DISABLED"
    GATEWAY_MOTION_DISABLED = "GATEWAY_MOTION_DISABLED"
    GATEWAY_SCOPE_MISMATCH = "GATEWAY_SCOPE_MISMATCH"
    MOTION_OUTCOME_UNKNOWN = "MOTION_OUTCOME_UNKNOWN"
