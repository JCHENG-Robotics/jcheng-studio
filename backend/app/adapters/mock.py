"""Deterministic in-process mock robot adapter.

No ROS, no Gazebo, no serial port. Every value is explicitly `simulated`.
Faults are injectable so the safety gate and the UI can be tested without
hardware.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Callable

from ..domain.enums import (
    BatteryStatus,
    CommandStage,
    ConnectionState,
    ErrorCode,
    FaultCode,
    FaultLevel,
    Mode,
    SourceLabel,
    StopState,
    Truth,
    Validity,
)
from ..domain.mapping import MappingConfig
from ..domain.models import (
    BatteryState,
    CameraStatus,
    Capabilities,
    ConnectionReport,
    Fault,
    GripperState,
    JointState,
    RobotState,
    SegmentStatus,
    StopResult,
    utc_now_iso,
)
from ..domain.takeover import TakeoverConfig
from .base import AdapterError, DispatchResult, RobotAdapter

FAULT_KINDS = {
    "timeout": (FaultLevel.FAULT, "模拟命令超时：命令状态应为 Unknown"),
    "disconnect": (FaultLevel.CRITICAL, "模拟连接断开：撤销运动许可"),
    "empty_feedback": (FaultLevel.FAULT, "模拟空反馈：显示 Unavailable，不得填 0"),
    "nan_feedback": (FaultLevel.FAULT, "模拟 NaN 反馈：标记 invalid"),
    "missing_servo": (FaultLevel.FAULT, "模拟舵机缺失：该关节不可用"),
    "driver_exit": (FaultLevel.CRITICAL, "模拟 Driver 退出：进入 Degraded/Fault"),
    "battery_low": (FaultLevel.WARNING, "模拟低电量：电压低于预警阈值"),
    "battery_missing": (FaultLevel.FAULT, "模拟电池数据缺失：显示 Unavailable"),
    "camera_offline": (FaultLevel.WARNING, "模拟相机离线：画面显示 Unavailable"),
}


class MockRobotAdapter(RobotAdapter):
    mode = Mode.MOCK
    source_label = SourceLabel.SIMULATION

    def __init__(
        self,
        mapping: MappingConfig,
        *,
        clock: Callable[[], float] | None = None,
        seed: int = 0,
        noise_rad: float = 0.0,
        takeover: TakeoverConfig | None = None,
    ) -> None:
        self.mapping = mapping
        self._clock = clock or time.monotonic
        self._random = random.Random(seed)
        self._noise_rad = max(0.0, float(noise_rad))
        self.takeover = takeover
        self.capabilities = Capabilities(
            hardware_motion=False,
            hardware_read=False,
            mock_faults=True,
            replay=True,
            # Advertise the same floor the pose validator applies: the
            # takeover contract's 1.0 s wins over the mapping's 0.5 s. The UI
            # uses this value to build a command, so reporting 0.5 s made a
            # "prepare view" when already at the view fail DURATION_RANGE.
            min_duration_s=(
                max(mapping.safety.min_duration_s, takeover.min_duration_s)
                if takeover is not None
                else mapping.safety.min_duration_s
            ),
            max_duration_s=(
                min(mapping.safety.max_duration_s, takeover.max_duration_s)
                if takeover is not None
                else mapping.safety.max_duration_s
            ),
            max_step_rad=(
                max(
                    joint.max_rad - joint.min_rad
                    for joint in mapping.joints.values()
                )
                if takeover is not None
                else mapping.safety.max_step_rad
            ),
            supervised_motion=False,
            single_joint_only=False,
            motion_mode="simulation",
            motion_scope="pose" if takeover is not None else "standard",
            max_velocity_rad_s=(
                takeover.max_velocity_rad_s if takeover is not None else None
            ),
            gripper_control=takeover is not None,
            pose_gate=takeover is not None,
        )
        self._targets: dict[str, float] = {name: 0.0 for name in mapping.joints}
        self._positions: dict[str, float] = {name: 0.0 for name in mapping.joints}
        if takeover is not None:
            open_rad = takeover.gripper.raw_to_rad(takeover.gripper.open_raw)
            self._targets[takeover.gripper.joint] = open_rad
            self._positions[takeover.gripper.joint] = open_rad
        self._motion: dict[str, Any] | None = None
        self._fault_kinds: set[str] = set()
        self._fault_joint: str | None = None
        self._faults: dict[str, Fault] = {}
        self._events: list[dict[str, Any]] = []
        self._last_command_id: str | None = None
        self._stop_state: StopState | None = None
        self._stop_at: float | None = None
        self._stop_command_id: str | None = None
        self._driver_online = True
        self._camera_started = self._clock()
        self._camera_frames = 0
        self._aux_camera_started = self._clock()
        self._aux_camera_frames = 0
        self._last_tick = self._clock()

    # ------------------------------------------------------------------ faults
    def inject(self, kind: str, *, joint: str | None = None) -> Fault:
        if kind not in FAULT_KINDS:
            raise AdapterError(
                ErrorCode.BAD_REQUEST.value,
                f"unknown mock fault: {kind}",
                {"allowed": sorted(FAULT_KINDS)},
            )
        if kind == "missing_servo" and joint not in self.mapping.joints:
            raise AdapterError(
                ErrorCode.UNKNOWN_JOINT.value,
                f"missing_servo requires a known joint, got {joint!r}",
            )
        level, message = FAULT_KINDS[kind]
        now = utc_now_iso()
        fault = Fault(
            fault_id=f"mock-{kind}",
            level=level,
            code=kind.upper(),
            message=message,
            source="mock_adapter",
            first_seen=now,
            last_seen=now,
            remediation="POST /api/mock/reset 清除注入故障",
            details={"joint": joint},
        )
        self._fault_kinds.add(kind)
        self._fault_joint = joint
        self._faults[kind] = fault
        if kind in {"disconnect", "driver_exit"}:
            self._motion = None
            self._stop_state = StopState.STOP_UNKNOWN
            self._stop_at = self._clock()
            if kind == "driver_exit":
                self._driver_online = False
        return fault

    def clear_fault(self, kind: str) -> bool:
        existed = kind in self._fault_kinds
        self._fault_kinds.discard(kind)
        self._faults.pop(kind, None)
        if kind in {"disconnect", "driver_exit"}:
            self._driver_online = True
            self._stop_state = None
            self._stop_at = None
        return existed

    def reset(self) -> None:
        self._fault_kinds.clear()
        self._faults.clear()
        self._fault_joint = None
        self._motion = None
        self._stop_state = None
        self._stop_at = None
        self._stop_command_id = None
        self._driver_online = True
        self._targets = {name: 0.0 for name in self.mapping.joints}
        self._positions = {name: 0.0 for name in self.mapping.joints}
        self._events.clear()
        self._last_command_id = None
        self._camera_started = self._clock()
        self._camera_frames = 0
        self._aux_camera_started = self._clock()
        self._aux_camera_frames = 0
        self._last_tick = self._clock()

    def active_faults(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": kind,
                "code": fault.code,
                "level": fault.level.value,
                "joint": fresh.details.get("joint"),
            }
            for kind, fault in self._faults.items()
            for fresh in [self._refresh_fault(fault)]
        ]

    def _refresh_fault(self, fault: Fault) -> Fault:
        fault.last_seen = utc_now_iso()
        return fault

    # ------------------------------------------------------------------ motion
    def dispatch(
        self,
        targets: list[dict[str, Any]],
        command_id: str,
        *,
        fast: bool = False,
    ) -> DispatchResult:
        if "disconnect" in self._fault_kinds:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.GATEWAY_UNAVAILABLE.value,
                "mock disconnect fault is active",
            )
        if "driver_exit" in self._fault_kinds or not self._driver_online:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.DRIVER_OFFLINE.value,
                "mock driver_exit fault is active",
            )

        target_map = {item["joint"]: float(item["target_rad"]) for item in targets}
        duration = max(float(item["duration_s"]) for item in targets)
        now = self._clock()
        self._motion = {
            "command_id": command_id,
            "start": now,
            "duration": duration,
            "start_positions": dict(self._positions),
            "targets": target_map,
            "timeout": "timeout" in self._fault_kinds,
            "emit_events": "timeout" not in self._fault_kinds,
        }
        for name, value in target_map.items():
            self._targets[name] = value
        self._last_command_id = command_id
        self._stop_state = None

        if "timeout" in self._fault_kinds:
            self._events.append(
                {
                    "stage": CommandStage.UNKNOWN.value,
                    "command_id": command_id,
                    "source": "mock_adapter",
                    "result": "no acknowledgement; position evidence unavailable",
                    "error_code": FaultCode.MOCK_TIMEOUT.value,
                    "details": {"fault": "timeout"},
                    "monotonic_ms": now * 1000.0,
                }
            )
            return DispatchResult(
                CommandStage.UNKNOWN,
                FaultCode.MOCK_TIMEOUT.value,
                "mock timeout: command dispatched but outcome is unknown",
                {"fault": "timeout"},
            )

        return DispatchResult(
            CommandStage.DISPATCHED,
            None,
            "mock command dispatched",
            {"emit_events": True},
        )

    def chassis_pulse(self, body: dict[str, Any]) -> dict[str, Any] | None:
        return {
            "status": "simulated",
            "verification": "simulated",
            "pulse": dict(body),
        }

    def stop(self, command_id: str) -> StopResult:
        now = self._clock()
        if "disconnect" in self._fault_kinds or "driver_exit" in self._fault_kinds:
            return StopResult(
                StopState.STOP_UNKNOWN,
                command_id,
                "stop request cannot be confirmed: mock connection fault",
                "no evidence of controller acknowledgement",
                requested_at=utc_now_iso(),
            )
        if self._motion is None:
            self._stop_state = StopState.STOP_OBSERVED
            self._stop_at = now
            return StopResult(
                StopState.STOP_OBSERVED,
                command_id,
                "no active motion; simulated position already at rest",
                "mock position unchanged over observation window; not a hardware emergency stop",
                requested_at=utc_now_iso(),
                observed_at=utc_now_iso(),
            )

        # Freeze at the interpolated position, not the previous target.
        self.tick()
        for name in self._positions:
            self._targets[name] = self._positions[name]
        self._motion = None
        self._stop_state = StopState.STOP_DISPATCHED
        self._stop_at = now
        self._stop_command_id = command_id
        return StopResult(
            StopState.STOP_DISPATCHED,
            command_id,
            "mock stop request dispatched",
            "software stop request; not a hardware emergency stop",
            requested_at=utc_now_iso(),
        )

    def tick(self) -> None:
        now = self._clock()
        if "disconnect" in self._fault_kinds:
            self._last_tick = now
            return

        motion = self._motion
        if motion is not None:
            elapsed = max(0.0, now - motion["start"])
            progress = min(1.0, elapsed / max(motion["duration"], 1e-9))
            for name, target in motion["targets"].items():
                start = motion["start_positions"].get(name, self._positions[name])
                self._positions[name] = start + (target - start) * progress
            if progress >= 1.0:
                for name, target in motion["targets"].items():
                    self._positions[name] = target
                self._motion = None
                if motion["emit_events"]:
                    for stage in (CommandStage.OBSERVED, CommandStage.COMPLETED):
                        self._events.append(
                            {
                                "stage": stage.value,
                                "command_id": motion["command_id"],
                                "source": "mock_adapter",
                                "result": "simulated position reached",
                                "error_code": None,
                                "details": {"progress": 1.0},
                                "monotonic_ms": now * 1000.0,
                            }
                        )

        if (
            self._stop_state is StopState.STOP_DISPATCHED
            and self._stop_at is not None
            and now - self._stop_at >= 0.2
        ):
            self._stop_state = StopState.STOP_OBSERVED
            self._events.append(
                {
                    "stage": StopState.STOP_OBSERVED.value,
                    "command_id": self._stop_command_id,
                    "source": "mock_adapter",
                    "result": "simulated motion stopped",
                    "error_code": None,
                    "details": {"stop_state": StopState.STOP_OBSERVED.value},
                    "monotonic_ms": now * 1000.0,
                }
            )
        self._last_tick = now

    # ------------------------------------------------------------------ state
    def _connection_state(self) -> ConnectionState:
        if "disconnect" in self._fault_kinds:
            return ConnectionState.DISCONNECTED
        if "driver_exit" in self._fault_kinds or not self._driver_online:
            return ConnectionState.DEGRADED
        if self._motion is not None:
            return ConnectionState.EXECUTING
        return ConnectionState.SIMULATION

    def _joint_state(self, name: str) -> JointState:
        joint = self.mapping.joints[name]
        target = self._targets[name]
        target_raw = int(round(joint.rad_to_raw(target)))
        disconnected = "disconnect" in self._fault_kinds or "driver_exit" in self._fault_kinds
        empty = "empty_feedback" in self._fault_kinds
        nan = "nan_feedback" in self._fault_kinds
        missing = "missing_servo" in self._fault_kinds and self._fault_joint == name

        if disconnected or empty or missing:
            position = None
            raw_position = None
            truth = Truth.UNKNOWN
            validity = Validity.UNAVAILABLE
            age = None
        elif nan:
            position = None
            raw_position = None
            truth = Truth.UNKNOWN
            validity = Validity.INVALID
            age = 0
        else:
            noise = self._random.uniform(-self._noise_rad, self._noise_rad) if self._noise_rad else 0.0
            position = self._positions[name] + noise
            raw_position = int(round(joint.rad_to_raw(position)))
            truth = Truth.SIMULATED
            validity = Validity.VALID
            age = 0

        return JointState(
            name=name,
            servo_id=joint.servo_id,
            target_rad=target,
            target_raw=target_raw,
            position_rad=position,
            raw_position=raw_position,
            truth=truth,
            validity=validity,
            sample_age_ms=age,
            source="mock_adapter",
            limit_min_rad=joint.min_rad,
            limit_max_rad=joint.max_rad,
            limit_verified=joint.verified,
        )

    def _gripper_state(self) -> GripperState | None:
        if self.takeover is None:
            return None
        gripper = self.takeover.gripper
        disconnected = "disconnect" in self._fault_kinds or "driver_exit" in self._fault_kinds
        position = self._positions.get(gripper.joint)
        available = position is not None and not disconnected
        return GripperState(
            joint=gripper.joint,
            servo_id=gripper.servo_id,
            available=available,
            target_rad=self._targets.get(gripper.joint),
            target_raw=(
                int(round(gripper.rad_to_raw(self._targets[gripper.joint])))
                if gripper.joint in self._targets
                else None
            ),
            position_rad=position if available else None,
            raw_position=(
                int(round(gripper.rad_to_raw(position)))
                if available and position is not None
                else None
            ),
            truth=Truth.SIMULATED if available else Truth.UNKNOWN,
            validity=Validity.VALID if available else Validity.UNAVAILABLE,
            sample_age_ms=0 if available else None,
            source="mock_adapter",
            min_rad=gripper.min_rad,
            max_rad=gripper.max_rad,
            open_raw=gripper.open_raw,
            close_raw=gripper.close_raw,
            measured_min_raw=gripper.measured_min_raw,
            measured_max_raw=gripper.measured_max_raw,
            completion=gripper.completion,
            notes=["模拟；完成按时间判定，未做力校验"],
        )

    def snapshot(self) -> RobotState:
        self.tick()
        now = self._clock()
        faults = [self._refresh_fault(f) for f in self._faults.values()]
        return RobotState(
            robot_id="armpi-ultra-01",
            mode=self.mode,
            connection_state=self._connection_state(),
            source_label=self.source_label,
            control_owner=None,
            received_at=utc_now_iso(),
            monotonic_ms=now * 1000.0,
            ros_time_ns=None,
            joints=[self._joint_state(name) for name in self.mapping.joints],
            faults=faults,
            mapping_version=self.mapping.mapping_version,
            limit_version=self.mapping.verification_status,
            mapping_verified=self.mapping.verified,
            capabilities=self.capabilities,
            stop_state=self._stop_state,
            last_command_id=self._last_command_id,
            gripper=self._gripper_state(),
        )

    def connection_report(self) -> ConnectionReport:
        disconnected = "disconnect" in self._fault_kinds
        driver_fault = "driver_exit" in self._fault_kinds or not self._driver_online
        feedback_fault = disconnected or "empty_feedback" in self._fault_kinds or "nan_feedback" in self._fault_kinds
        segments = [
            SegmentStatus(
                name="serial",
                state="simulated",
                detail="Mock 适配器；未打开厂商串口",
                evidence="ARMPI_MODE=mock",
            ),
            SegmentStatus(
                name="driver",
                state="offline" if driver_fault else "simulated",
                detail="Mock 驱动状态",
                evidence="no vendor ROS node",
            ),
            SegmentStatus(
                name="gateway",
                state="offline" if disconnected else "online",
                detail="进程内 Mock 适配器；无 ROS 网关",
                evidence="mock adapter",
            ),
            SegmentStatus(
                name="feedback",
                state="unavailable" if feedback_fault else "simulated",
                detail="模拟位置，不是传感器反馈",
                evidence="truth=simulated",
            ),
        ]
        return ConnectionReport(
            segments=segments,
            serial_available=False,
            driver_online=not driver_fault,
            gateway_online=not disconnected,
            feedback_fresh=not feedback_fault,
            notes=[
                "模拟：未访问串口、ROS 节点或硬件。",
                "Mock 位置是合成值，不能当作硬件反馈。",
            ],
        )

    def battery(self) -> BatteryState:
        now = self._clock()
        unavailable = (
            "disconnect" in self._fault_kinds
            or "driver_exit" in self._fault_kinds
            or "battery_missing" in self._fault_kinds
        )
        if unavailable:
            return BatteryState(
                available=False,
                voltage_mv=None,
                voltage_v=None,
                percent_estimate=None,
                cell_count=2,
                status=BatteryStatus.UNAVAILABLE,
                truth=Truth.UNKNOWN,
                validity=Validity.UNAVAILABLE,
                source="mock_adapter",
                topic="mock://vendor-battery",
                received_at=utc_now_iso(),
                sample_age_ms=None,
                notes=["模拟：注入故障导致电池不可用。"],
            )

        # 2S Li-ion: 6.4 V empty, 8.4 V full. The mock starts near the measured
        # baseline (7.65 V) and drifts slowly so the UI has live-looking data.
        drift = int(round(18 * math.sin(now / 37.0) + 7 * math.sin(now / 11.0)))
        voltage_mv = 7650 + drift
        if "battery_low" in self._fault_kinds:
            voltage_mv = 6750 + drift // 2
        percent = max(0.0, min(100.0, (voltage_mv - 6400.0) / (8400.0 - 6400.0) * 100.0))
        status = (
            BatteryStatus.CRITICAL
            if percent < 10.0
            else BatteryStatus.LOW
            if percent < 25.0
            else BatteryStatus.OK
        )
        return BatteryState(
            available=True,
            voltage_mv=voltage_mv,
            voltage_v=round(voltage_mv / 1000.0, 3),
            percent_estimate=round(percent, 1),
            cell_count=2,
            status=status,
            truth=Truth.SIMULATED,
            validity=Validity.VALID,
            source="mock_adapter",
            topic="mock://vendor-battery",
            received_at=utc_now_iso(),
            sample_age_ms=0,
            notes=["模拟：合成的 2S 电压；不是硬件测量值。"],
        )

    def camera_status(self) -> CameraStatus:
        now = self._clock()
        unavailable = (
            "disconnect" in self._fault_kinds
            or "driver_exit" in self._fault_kinds
            or "camera_offline" in self._fault_kinds
        )
        if unavailable:
            return CameraStatus(
                available=False,
                truth=Truth.UNKNOWN,
                validity=Validity.UNAVAILABLE,
                source="mock_adapter",
                topic="/depth_cam/rgb/image_raw",
                width=None,
                height=None,
                encoding=None,
                fps=0.0,
                last_frame_at=None,
                sample_age_ms=None,
                frame_count=self._camera_frames,
                notes=["模拟：注入故障导致相机不可用。"],
            )

        elapsed = max(0.0, now - self._camera_started)
        self._camera_frames = max(self._camera_frames, int(elapsed * 8.0) + 1)
        return CameraStatus(
            available=True,
            truth=Truth.SIMULATED,
            validity=Validity.VALID,
            source="mock_adapter",
            topic="/depth_cam/rgb/image_raw",
            width=640,
            height=400,
            encoding="bgr8",
            fps=8.0,
            last_frame_at=utc_now_iso(),
            sample_age_ms=0,
            frame_count=self._camera_frames,
            notes=["模拟：生成的占位画面；未访问相机设备。"],
        )

    def camera_snapshot(self) -> tuple[bytes, str] | None:
        status = self.camera_status()
        if not status.available:
            return None
        battery = self.battery()
        battery_text = (
            f"{battery.voltage_v:.2f} V / {battery.percent_estimate:.0f}%"
            if battery.available and battery.voltage_v is not None
            else "battery unavailable"
        )
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">
  <rect width="640" height="400" fill="#070b10"/>
  <g stroke="#1d2a38" stroke-width="1">
    <path d="M0 100 H640 M0 200 H640 M0 300 H640 M160 0 V400 M320 0 V400 M480 0 V400"/>
  </g>
  <g stroke="#2bd4e8" stroke-width="1.5" fill="none" opacity="0.8">
    <path d="M320 170 V230 M290 200 H350"/>
    <circle cx="320" cy="200" r="46" stroke-dasharray="4 6"/>
  </g>
  <text x="24" y="42" fill="#e8eef6" font-family="monospace" font-size="24" font-weight="700">MOCK CAMERA</text>
  <text x="24" y="66" fill="#22d3ee" font-family="monospace" font-size="13">SIMULATION — no camera device accessed</text>
  <text x="24" y="352" fill="#b6c3d4" font-family="monospace" font-size="14">{status.width}×{status.height} · {status.fps:.0f} fps · {status.encoding}</text>
  <text x="24" y="374" fill="#8192a8" font-family="monospace" font-size="12">frame {status.frame_count} · {battery_text}</text>
  <text x="616" y="42" fill="#8192a8" font-family="monospace" font-size="12" text-anchor="end">{utc_now_iso()}</text>
</svg>"""
        return svg.encode("utf-8"), "image/svg+xml"

    def aux_camera_status(self) -> CameraStatus:
        now = self._clock()
        unavailable = (
            "disconnect" in self._fault_kinds
            or "driver_exit" in self._fault_kinds
            or "camera_offline" in self._fault_kinds
        )
        if unavailable:
            return CameraStatus(
                available=False,
                truth=Truth.UNKNOWN,
                validity=Validity.UNAVAILABLE,
                source="mock_adapter",
                topic="mock://aux-camera",
                width=None,
                height=None,
                encoding=None,
                fps=0.0,
                last_frame_at=None,
                sample_age_ms=None,
                frame_count=self._aux_camera_frames,
                notes=["模拟：注入故障导致辅助相机不可用。"],
            )

        elapsed = max(0.0, now - self._aux_camera_started)
        self._aux_camera_frames = max(self._aux_camera_frames, int(elapsed * 10.0) + 1)
        return CameraStatus(
            available=True,
            truth=Truth.SIMULATED,
            validity=Validity.VALID,
            source="mock_adapter",
            topic="mock://aux-camera",
            width=640,
            height=480,
            encoding="yuyv",
            fps=10.0,
            last_frame_at=utc_now_iso(),
            sample_age_ms=0,
            frame_count=self._aux_camera_frames,
            notes=[
                "模拟：生成的辅助占位画面；未打开 UVC 设备。"
            ],
        )

    def aux_camera_snapshot(self) -> tuple[bytes, str] | None:
        status = self.aux_camera_status()
        if not status.available:
            return None
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
  <rect width="640" height="480" fill="#060c10"/>
  <g stroke="#1d2a38" stroke-width="1">
    <path d="M0 120 H640 M0 240 H640 M0 360 H640 M160 0 V480 M320 0 V480 M480 0 V480"/>
  </g>
  <g stroke="#f3b34c" stroke-width="1.5" fill="none" opacity="0.8">
    <path d="M320 200 V280 M280 240 H360"/>
    <rect x="250" y="170" width="140" height="140" stroke-dasharray="4 6"/>
  </g>
  <text x="24" y="46" fill="#e8eef6" font-family="monospace" font-size="24" font-weight="700">MOCK AUX CAMERA</text>
  <text x="24" y="70" fill="#f3b34c" font-family="monospace" font-size="13">SIMULATION — mock aux camera, no device accessed</text>
  <text x="24" y="420" fill="#b6c3d4" font-family="monospace" font-size="14">{status.width}×{status.height} · {status.fps:.0f} fps · {status.encoding}</text>
  <text x="24" y="444" fill="#8192a8" font-family="monospace" font-size="12">frame {status.frame_count} · D435i color stream</text>
  <text x="616" y="46" fill="#8192a8" font-family="monospace" font-size="12" text-anchor="end">{utc_now_iso()}</text>
</svg>"""
        return svg.encode("utf-8"), "image/svg+xml"

    def drain_events(self) -> list[dict[str, Any]]:
        events = self._events
        self._events = []
        return events
