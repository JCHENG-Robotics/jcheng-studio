"""Hardware adapter for the read-only gateway and supervised motion path.

When `ARMPI_GATEWAY_URL` is configured, this adapter reads live battery, camera,
and joint state from the on-robot `armpi_debug_gateway`. It never opens a
serial port and never talks to the vendor driver directly.

Motion is disabled unless the backend was started with
``ARMPI_HARDWARE_MOTION=1``. For unverified limits that flag is only accepted
together with the fixed ``ARMPI_SUPERVISED_MOTION_ACK`` phrase; the adapter then
enforces single-joint commands, a smaller step and a longer duration floor, and
the gateway independently re-validates the command against measured pulses.
"""

from __future__ import annotations

import functools
import threading
import time
from dataclasses import replace
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
from ..domain.motion import EffectiveMotionLimits, effective_motion_limits
from ..domain.takeover import TakeoverConfig
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
from .base import AdapterError, DispatchResult, HardwareMotionDisabled, RobotAdapter
from .gateway import GatewayClient, GatewayError

# Gateway evidence notes are kept in the payload but shown with a Chinese
# explanation; notes we do not know pass through unchanged.
_GATEWAY_NOTE_LABELS = {
    "aux camera is disabled (--no-aux-camera).": (
        "辅助相机已禁用（--no-aux-camera）。"
    ),
    "opencv is not available in this Python environment.": (
        "该 Python 环境没有 OpenCV。"
    ),
    "cv_bridge is not installed; frames were decoded with the numpy fallback.": (
        "未安装 cv_bridge；帧由 numpy 回退解码。"
    ),
    "No battery message received yet.": "尚未收到电池消息。",
    "ROS colour topic from ext_cam_node (single UVC owner); depth and IR need librealsense.": (
        "ex_cam_node 提供的 ROS 彩色话题（UVC 唯一持有者）；"
        "深度和 IR 需要 librealsense。"
    ),
}


def _localize_gateway_note(note: str) -> str:
    return _GATEWAY_NOTE_LABELS.get(note, note)


def _locked(method):
    """Serialize gateway state mutations across the ticker and HTTP threads."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class HardwareAdapter(RobotAdapter):
    mode = Mode.HARDWARE
    source_label = SourceLabel.LIVE

    def __init__(
        self,
        mapping: MappingConfig,
        *,
        clock: Callable[[], float] | None = None,
        gateway_url: str | None = None,
        gateway_token: str | None = None,
        camera_gateway_url: str | None = None,
        camera_gateway_token: str | None = None,
        motion_enabled: bool = False,
        supervised: bool = False,
        motion_scope: str = "step",
        takeover: TakeoverConfig | None = None,
    ) -> None:
        self.mapping = mapping
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._gateway = (
            GatewayClient(gateway_url, token=gateway_token) if gateway_url else None
        )
        # The motion gateway (pose instance) may keep its camera off; the
        # persistent read-only gateway owns the exclusive UVC aux device. Route
        # camera endpoints separately when configured.
        self._camera_gateway = (
            GatewayClient(
                camera_gateway_url,
                token=camera_gateway_token or gateway_token,
            )
            if camera_gateway_url
            else self._gateway
        )
        self.motion_enabled = bool(motion_enabled and gateway_url)
        self.supervised = bool(supervised and self.motion_enabled)
        self.takeover = takeover if (takeover is not None and self.motion_enabled) else None
        self.motion_scope = (
            "pose"
            if self.takeover is not None
            else ("step" if self.motion_enabled else "read-only")
        )
        self.motion_limits: EffectiveMotionLimits = effective_motion_limits(
            mapping,
            supervised=self.supervised,
            takeover=self.takeover,
        )
        self.capabilities = Capabilities(
            hardware_motion=self.motion_enabled,
            hardware_read=bool(gateway_url),
            mock_faults=False,
            replay=True,
            min_duration_s=self.motion_limits.min_duration_s,
            max_duration_s=self.motion_limits.max_duration_s,
            max_step_rad=self.motion_limits.max_step_rad,
            supervised_motion=self.supervised,
            single_joint_only=self.motion_limits.single_joint_only,
            motion_mode=(
                self.motion_limits.mode_label
                if self.motion_enabled
                else "read-only"
            ),
            motion_scope=self.motion_scope,
            max_velocity_rad_s=self.motion_limits.max_velocity_rad_s,
            gripper_control=self.motion_limits.gripper_enabled,
            pose_gate=self.takeover is not None,
        )
        self._health_cache: dict[str, Any] | None = None
        self._health_at = -1e9
        self._state_cache: dict[str, Any] | None = None
        self._state_at = -1e9
        self._tf_cache: dict[str, Any] | None = None
        self._tf_at = -1e9
        self._motion_status_at = -1e9
        self._motion_status_cache: dict[str, Any] | None = None
        self._active: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        self._motion_latched: Fault | None = None
        self._last_command_id: str | None = None
        self._not_implemented = Fault(
            fault_id="hardware-motion-disabled",
            level=FaultLevel.WARNING,
            code=FaultCode.HARDWARE_MOTION_DISABLED.value,
            message=(
                "硬件运动未启用；网关为只读"
                if gateway_url
                else "未配置 ARMPI_GATEWAY_URL；运动已禁用"
            ),
            source="hardware_adapter",
            first_seen=utc_now_iso(),
            last_seen=utc_now_iso(),
            remediation="完成关节映射验证并获得评审批准后再启用运动",
        )

    # ------------------------------------------------------------------ gateway
    def _health(self) -> dict[str, Any] | None:
        if self._gateway is None:
            return None
        now = self._clock()
        if self._health_at >= 0 and now - self._health_at < 2.0:
            return self._health_cache
        try:
            data: dict[str, Any] | None = self._gateway.get_json("/health")
        except GatewayError:
            data = None
        self._health_cache = data
        self._health_at = now
        return data

    def _state_payload(self) -> dict[str, Any] | None:
        if self._gateway is None:
            return None
        now = self._clock()
        if self._state_at >= 0 and now - self._state_at < 0.2:
            return self._state_cache
        try:
            data: dict[str, Any] | None = self._gateway.get_json("/state")
        except GatewayError:
            data = None
        self._state_cache = data
        self._state_at = now
        return data

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _optional_int(value: Any) -> int | None:
        return int(value) if value is not None else None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return float(value) if value is not None else None

    def _battery_unavailable(self, note: str) -> BatteryState:
        return BatteryState(
            available=False,
            voltage_mv=None,
            voltage_v=None,
            percent_estimate=None,
            cell_count=2,
            status=BatteryStatus.UNAVAILABLE,
            truth=Truth.UNKNOWN,
            validity=Validity.UNAVAILABLE,
            source="hardware_gateway",
            topic="gateway-battery",
            received_at=utc_now_iso(),
            sample_age_ms=None,
            notes=[note],
        )

    def _camera_unavailable(
        self, note: str, *, topic: str = "/depth_cam/rgb/image_raw"
    ) -> CameraStatus:
        return CameraStatus(
            available=False,
            truth=Truth.UNKNOWN,
            validity=Validity.UNAVAILABLE,
            source="hardware_gateway",
            topic=topic,
            width=None,
            height=None,
            encoding=None,
            fps=None,
            last_frame_at=None,
            sample_age_ms=None,
            frame_count=0,
            notes=[note],
        )

    def battery(self) -> BatteryState:
        if self._gateway is None:
            return self._battery_unavailable("ARMPI_GATEWAY_URL is not configured")
        try:
            data = self._gateway.get_json("/battery")
        except GatewayError as exc:
            return self._battery_unavailable(str(exc))
        try:
            available = bool(data.get("available", data.get("voltage_mv") is not None))
            voltage_mv = self._optional_int(data.get("voltage_mv"))
            voltage_v = self._optional_float(data.get("voltage_v"))
            if voltage_v is None and voltage_mv is not None:
                voltage_v = round(voltage_mv / 1000.0, 3)
            status = BatteryStatus(data.get("status", "unavailable"))
            validity = Validity(data.get("validity", "valid" if available else "unavailable"))
            truth = Truth(data.get("truth", "measured" if available else "unknown"))
            return BatteryState(
                available=available,
                voltage_mv=voltage_mv,
                voltage_v=voltage_v,
                percent_estimate=self._optional_float(data.get("percent_estimate")),
                cell_count=int(data.get("cell_count", 2)),
                status=status,
                truth=truth,
                validity=validity,
                source=str(data.get("source", "armpi_debug_gateway")),
                topic=str(data.get("topic", "gateway-battery")),
                received_at=str(data.get("received_at") or utc_now_iso()),
                sample_age_ms=self._optional_int(data.get("sample_age_ms")),
                notes=[
                    _localize_gateway_note(str(item))
                    for item in data.get("notes", [])
                ],
            )
        except (TypeError, ValueError, KeyError) as exc:
            return self._battery_unavailable(f"invalid gateway battery payload: {exc}")

    def _camera_from_gateway(
        self, path: str, *, default_topic: str, label: str
    ) -> CameraStatus:
        if self._camera_gateway is None:
            return self._camera_unavailable(
                "未配置相机网关或 ARMPI_GATEWAY_URL", topic=default_topic
            )
        try:
            data = self._camera_gateway.get_json(path)
        except GatewayError as exc:
            return self._camera_unavailable(str(exc), topic=default_topic)
        try:
            available = bool(data.get("available", False))
            return CameraStatus(
                available=available,
                truth=Truth(data.get("truth", "measured" if available else "unknown")),
                validity=Validity(data.get("validity", "valid" if available else "unavailable")),
                source=str(data.get("source", "armpi_debug_gateway")),
                topic=str(data.get("topic", default_topic)),
                width=self._optional_int(data.get("width")),
                height=self._optional_int(data.get("height")),
                encoding=str(data["encoding"]) if data.get("encoding") is not None else None,
                fps=self._optional_float(data.get("fps")),
                last_frame_at=str(data["last_frame_at"]) if data.get("last_frame_at") else None,
                sample_age_ms=self._optional_int(data.get("sample_age_ms")),
                frame_count=int(data.get("frame_count", 0)),
                notes=[
                    _localize_gateway_note(str(item))
                    for item in data.get("notes", [])
                ],
            )
        except (TypeError, ValueError, KeyError) as exc:
            return self._camera_unavailable(
                f"invalid gateway {label} payload: {exc}", topic=default_topic
            )

    def camera_status(self) -> CameraStatus:
        return self._camera_from_gateway(
            "/camera/status",
            default_topic="/depth_cam/rgb/image_raw",
            label="camera",
        )

    def camera_snapshot(self) -> tuple[bytes, str] | None:
        if self._camera_gateway is None:
            return None
        try:
            return self._camera_gateway.get_bytes("/camera/snapshot")
        except GatewayError:
            return None

    def aux_camera_status(self) -> CameraStatus:
        return self._camera_from_gateway(
            "/aux-camera/status", default_topic="gateway-aux-camera", label="aux camera"
        )

    def aux_camera_snapshot(self) -> tuple[bytes, str] | None:
        if self._camera_gateway is None:
            return None
        try:
            return self._camera_gateway.get_bytes("/aux-camera/snapshot")
        except GatewayError:
            return None

    def tf_snapshot(self) -> dict[str, Any] | None:
        if self._gateway is None:
            return None
        now = self._clock()
        if self._tf_cache is not None and now - self._tf_at < 0.5:
            return self._tf_cache
        try:
            data = self._gateway.get_json("/tf")
        except GatewayError:
            data = None
        self._tf_cache = data
        self._tf_at = now
        return data

    def pointcloud(self) -> dict[str, Any] | None:
        """Fetch the gateway's registered cloud as float32 XYZ triples."""
        if self._gateway is None:
            return None
        try:
            meta = self._gateway.get_json("/pointcloud/meta", timeout_s=20.0)
        except GatewayError as exc:
            return {
                "meta": {"available": False, "error": str(exc)},
                "points": None,
            }
        if not isinstance(meta, dict) or not meta.get("available"):
            return {"meta": meta if isinstance(meta, dict) else {}, "points": None}
        try:
            body, _content_type = self._gateway.get_bytes(
                "/pointcloud", timeout_s=20.0
            )
            import numpy as np

            points = np.frombuffer(body, dtype="<f4")
            if points.size % 3 != 0:
                raise ValueError("point cloud payload is not XYZ triples")
            return {
                "meta": meta,
                "points": points.reshape(-1, 3),
            }
        except (GatewayError, ValueError) as exc:
            return {"meta": {**meta, "available": False, "error": str(exc)}, "points": None}

    def chassis_pulse(self, body: dict[str, Any]) -> dict[str, Any]:
        """Forward one bounded chassis pulse to the gateway (no retry)."""
        if not self.motion_enabled:
            raise HardwareMotionDisabled(
                "hardware motion is disabled on this adapter"
            )
        if self._gateway is None:
            raise HardwareMotionDisabled("no gateway client configured")
        try:
            return self._gateway.post_json(
                "/chassis/pulse", body, timeout_s=20.0
            )
        except GatewayError as exc:
            raise AdapterError(
                exc.code or ErrorCode.GATEWAY_UNAVAILABLE.value,
                f"chassis pulse was rejected: {exc}",
                {"gateway_status": exc.status, "gateway_code": exc.code},
            ) from exc

    def last_actual(self) -> dict[str, Any] | None:
        """Return the gateway's last bus-servo read, regardless of age."""
        if self._gateway is None:
            return None
        payload = self._state_payload()
        actual = payload.get("actual") if isinstance(payload, dict) else None
        if isinstance(actual, dict) and actual:
            return actual
        return None

    # ------------------------------------------------------------------ state
    def tick(self) -> None:
        return None

    def _unavailable_joint(self, name: str, source: str) -> JointState:
        joint = self.mapping.joints[name]
        return JointState(
            name=name,
            servo_id=joint.servo_id,
            target_rad=None,
            target_raw=None,
            position_rad=None,
            raw_position=None,
            truth=Truth.UNKNOWN,
            validity=Validity.UNAVAILABLE,
            sample_age_ms=None,
            source=source,
            limit_min_rad=joint.min_rad,
            limit_max_rad=joint.max_rad,
            limit_verified=joint.verified,
        )

    def _fresh_actual(self, actual: dict[str, Any] | None) -> dict[str, Any] | None:
        """Return the bus read only while it is fresh enough for the gate.

        The bus read is a one-shot snapshot captured after a move. It must not
        replace the 50 Hz command cache as the *continuous* position source:
        its age would trip the feedback-freshness check before the next move.
        """
        if not isinstance(actual, dict) or actual.get("position_rad") is None:
            return None
        age = self._optional_int(actual.get("sample_age_ms"))
        if age is None or age > self.mapping.safety.feedback_max_age_ms:
            return None
        return actual

    def _joint_from_gateway(
        self,
        name: str,
        item: dict[str, Any] | None,
        measured: dict[str, Any] | None = None,
        actual: dict[str, Any] | None = None,
    ) -> JointState:
        """Build a joint from the bus read, the command cache or the echo.

        Priority: a **fresh** ``GetBusServoState`` read (``actual``,
        ``measured_on_demand``), then the vendor command cache
        (``servo_states``, truth ``commanded``), then the ``joint_states``
        echo. The cache is **not** a measured position; the gateway verifies
        each move with a bus read.
        """
        joint = self.mapping.joints[name]
        actual = self._fresh_actual(actual)
        use_actual = actual is not None
        use_measured = (
            isinstance(measured, dict) and measured.get("position_rad") is not None
        )
        if not use_actual and not use_measured and (
            not isinstance(item, dict) or item.get("position_rad") is None
        ):
            return self._unavailable_joint(name, "hardware_gateway_unavailable")
        try:
            if use_actual:
                assert isinstance(actual, dict)
                block = actual
                default_truth = "measured_on_demand"
            elif use_measured:
                assert isinstance(measured, dict)
                block = measured
                default_truth = "commanded"
            else:
                assert isinstance(item, dict)
                block = item
                default_truth = "commanded"
            position = float(block["position_rad"])
            target = (
                float(item["target_rad"])
                if isinstance(item, dict) and item.get("target_rad") is not None
                else position
            )
            raw = (
                int(block["raw_position"])
                if block.get("raw_position") is not None
                else int(round(joint.rad_to_raw(position)))
            )
            truth = Truth(block.get("truth", default_truth))
            validity = Validity(block.get("validity", "valid"))
            age = self._optional_int(block.get("sample_age_ms"))
            source = str(block.get("source", "gateway:/state"))
            return JointState(
                name=name,
                servo_id=joint.servo_id,
                target_rad=target,
                target_raw=int(round(joint.rad_to_raw(target))),
                position_rad=position,
                raw_position=raw,
                truth=truth,
                validity=validity,
                sample_age_ms=age,
                source=source,
                limit_min_rad=joint.min_rad,
                limit_max_rad=joint.max_rad,
                limit_verified=joint.verified,
            )
        except (TypeError, ValueError, KeyError):
            return self._unavailable_joint(name, "hardware_gateway_invalid_payload")

    def _gripper_from_gateway(
        self,
        measured: dict[str, Any] | None,
        commanded: dict[str, Any] | None = None,
        actual: dict[str, Any] | None = None,
    ) -> GripperState | None:
        """Build the r_joint telemetry block, or None outside pose scope."""
        if self.takeover is None:
            return None
        gripper = self.takeover.gripper
        base = GripperState(
            joint=gripper.joint,
            servo_id=gripper.servo_id,
            available=False,
            target_rad=None,
            target_raw=None,
            position_rad=None,
            raw_position=None,
            truth=Truth.UNKNOWN,
            validity=Validity.UNAVAILABLE,
            sample_age_ms=None,
            source="gateway:/state#measured",
            min_rad=gripper.min_rad,
            max_rad=gripper.max_rad,
            open_raw=gripper.open_raw,
            close_raw=gripper.close_raw,
            measured_min_raw=gripper.measured_min_raw,
            measured_max_raw=gripper.measured_max_raw,
            completion=gripper.completion,
            notes=[
                "completion is timed; the fingers stall on contact",
                f"measured register range {gripper.measured_min_raw}"
                f"..{gripper.measured_max_raw} (command "
                f"{gripper.open_raw}..{gripper.close_raw})",
            ],
        )
        actual = self._fresh_actual(actual)
        use_actual = actual is not None
        use_measured = (
            isinstance(measured, dict) and measured.get("position_rad") is not None
        )
        if not use_actual and not use_measured:
            return base
        block = actual if use_actual else measured
        assert isinstance(block, dict)
        try:
            position = float(block["position_rad"])
            raw = (
                int(block["raw_position"])
                if block.get("raw_position") is not None
                else int(round(gripper.rad_to_raw(position)))
            )
            commanded_rad = (
                float(commanded["position_rad"])
                if isinstance(commanded, dict) and commanded.get("position_rad") is not None
                else position
            )
            return replace(
                base,
                available=True,
                position_rad=position,
                raw_position=raw,
                target_rad=commanded_rad,
                target_raw=int(round(gripper.rad_to_raw(commanded_rad))),
                truth=Truth(
                    block.get(
                        "truth",
                        "measured_on_demand" if use_actual else "commanded",
                    )
                ),
                validity=Validity(block.get("validity", "valid")),
                sample_age_ms=self._optional_int(block.get("sample_age_ms")),
                source=str(block.get("source", "gateway:/state#measured")),
            )
        except (TypeError, ValueError, KeyError):
            return replace(
                base,
                notes=base.notes + ["invalid gripper payload"],
            )

    def _motion_ready(self) -> bool:
        if not self.motion_enabled:
            return False
        health = self._health()
        if not health or not health.get("motion_enabled"):
            return False
        mode = health.get("motion_mode")
        if self.motion_scope == "pose":
            # A pose-capable backend must never drive a gateway built with the
            # 0.10 rad / single-joint contract.
            return mode == "pose"
        return mode in (None, "step")

    def _scope_mismatch_fault(self) -> Fault | None:
        if not self.motion_enabled or self.motion_scope != "pose":
            return None
        health = self._health()
        if not health or not health.get("motion_enabled"):
            return None
        mode = health.get("motion_mode")
        if mode == "pose":
            return None
        now = utc_now_iso()
        return Fault(
            fault_id="gateway-scope-mismatch",
            level=FaultLevel.FAULT,
            code=FaultCode.GATEWAY_SCOPE_MISMATCH.value,
            message=(
                "后端为姿态接管，但网关报告 "
                f"motion_mode={mode!r}；已阻止下发"
            ),
            source="hardware_adapter",
            first_seen=now,
            last_seen=now,
            remediation=(
                "生成姿态运动配置并用它重启运动网关，或以 "
                "ARMPI_MOTION_SCOPE=step 启动后端"
            ),
            details={"gateway_motion_mode": mode, "backend_scope": self.motion_scope},
        )

    def _motion_disabled_fault(self) -> Fault:
        now = utc_now_iso()
        return Fault(
            fault_id="gateway-motion-disabled",
            level=FaultLevel.FAULT,
            code=FaultCode.GATEWAY_MOTION_DISABLED.value,
            message=(
                "后端已启用硬件运动，但网关报告 "
                "motion_enabled=false"
            ),
            source="hardware_adapter",
            first_seen=now,
            last_seen=now,
            remediation=(
                "用 --enable-motion 和运动限位文件启动受监督网关，"
                "或关闭 ARMPI_HARDWARE_MOTION"
            ),
        )

    @_locked
    def snapshot(self) -> RobotState:
        now_monotonic = self._clock()
        self._poll_motion(now_monotonic)
        health = self._health()
        online = health is not None
        joints_payload: dict[str, Any] = {}
        measured_payload: dict[str, Any] = {}
        actual_payload: dict[str, Any] = {}
        state_payload: dict[str, Any] = {}
        if online:
            state_payload = self._state_payload() or {}
            raw_joints = state_payload.get("joints")
            if isinstance(raw_joints, dict):
                joints_payload = raw_joints
            raw_measured = state_payload.get("measured")
            if isinstance(raw_measured, dict):
                measured_payload = raw_measured
            raw_actual = state_payload.get("actual")
            if isinstance(raw_actual, dict):
                actual_payload = raw_actual

        faults: list[Fault] = []
        if self.motion_enabled:
            if online and not self._motion_ready():
                mismatch = self._scope_mismatch_fault()
                faults.append(mismatch or self._motion_disabled_fault())
        else:
            self._not_implemented.last_seen = utc_now_iso()
            faults.append(self._not_implemented)
        if self._motion_latched is not None:
            self._motion_latched.last_seen = utc_now_iso()
            faults.append(self._motion_latched)
        if self._gateway is not None and not online:
            now = utc_now_iso()
            faults.append(
                Fault(
                    fault_id="gateway-offline",
                    level=FaultLevel.WARNING,
                    code="GATEWAY_OFFLINE",
                    message=f"网关不可达：{self._gateway.base_url}",
                    source="hardware_adapter",
                    first_seen=now,
                    last_seen=now,
                    remediation="在机器人上启动 armpi_debug_gateway，或修正 ARMPI_GATEWAY_URL",
                )
            )

        capabilities = replace(
            self.capabilities,
            hardware_motion=self._motion_ready(),
        )
        return RobotState(
            robot_id="armpi-ultra-01",
            mode=self.mode,
            connection_state=(
                ConnectionState.EXECUTING
                if self._active is not None
                else (
                    ConnectionState.READ_ONLY
                    if online
                    else ConnectionState.DISCONNECTED
                )
            ),
            source_label=self.source_label,
            control_owner=None,
            received_at=utc_now_iso(),
            monotonic_ms=now_monotonic * 1000.0,
            ros_time_ns=(
                self._optional_int(state_payload.get("ros_time_ns"))
                if online
                else None
            ),
            joints=[
                self._joint_from_gateway(
                    name,
                    joints_payload.get(name),
                    measured_payload.get(name),
                    actual_payload.get(name),
                )
                for name in self.mapping.joints
            ],
            faults=faults,
            mapping_version=self.mapping.mapping_version,
            limit_version=self.mapping.verification_status,
            mapping_verified=self.mapping.verified,
            capabilities=capabilities,
            stop_state=None,
            last_command_id=self._active["command_id"] if self._active else self._last_command_id,
            gripper=self._gripper_from_gateway(
                measured_payload.get("r_joint"),
                joints_payload.get("r_joint"),
                actual_payload.get("r_joint"),
            ),
        )

    @_locked
    def connection_report(self) -> ConnectionReport:
        health = self._health()
        if self._gateway is None:
            segments = [
                SegmentStatus("serial", "unknown", "未探测", "该后端不直接打开串口；由网关访问驱动"),
                SegmentStatus("driver", "unknown", "未探测", "gateway not configured"),
                SegmentStatus("gateway", "offline", "未配置 ARMPI_GATEWAY_URL", "no gateway client"),
                SegmentStatus("feedback", "unavailable", "无网关", "no feedback source"),
            ]
            return ConnectionReport(
                segments=segments,
                serial_available=False,
                driver_online=False,
                gateway_online=False,
                feedback_fresh=False,
                notes=[
                    "未配置网关时，硬件模式只读。",
                    "该后端不直接打开串口，也不启动 ROS 节点。",
                ],
            )

        online = health is not None
        ros_ready = bool(health.get("ros_ready")) if health else False
        battery_ready = bool(health.get("battery_ready")) if health else False
        camera_ready = bool(health.get("camera_ready")) if health else False
        joints_ready = bool(health.get("joints_ready")) if health else False
        measured_ready = bool(health.get("measured_ready")) if health else False
        segments = [
            SegmentStatus("serial", "unknown", "由厂商栈占用", "网关是串口的唯一持有者"),
            SegmentStatus(
                "driver",
                "online" if ros_ready else "offline",
                "厂商 ROS 栈（经网关）",
                "GET /health ros_ready",
            ),
            SegmentStatus(
                "gateway",
                "online" if online else "offline",
                f"{self._gateway.base_url}",
                "GET /health",
            ),
            SegmentStatus(
                "feedback",
                "online" if (joints_ready or camera_ready or battery_ready) else "unavailable",
                "经网关读取电池/相机/关节状态",
                "GET /state, /battery, /camera/status",
            ),
        ]
        return ConnectionReport(
            segments=segments,
            serial_available=False,
            driver_online=ros_ready,
            gateway_online=online,
            feedback_fresh=measured_ready if self.motion_enabled else joints_ready,
            notes=(
                [
                    "Supervised gateway motion is enabled for this backend instance.",
                    "joint_states is a command echo; servo_states is the measured position source.",
                ]
                if self.motion_enabled
                else [
                    "Gateway is read-only; motion endpoints answer MOTION_DISABLED.",
                    "joint_states is a command echo, not encoder feedback.",
                ]
            ),
        )

    # ------------------------------------------------------------------ motion
    def _event(
        self,
        stage: CommandStage,
        command_id: str | None,
        result: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "stage": stage.value,
            "command_id": command_id,
            "source": "hardware_adapter",
            "result": result,
            "error_code": error_code,
            "details": details or {},
            "monotonic_ms": self._clock() * 1000.0,
        }

    def _finish_unknown(
        self,
        reason: str,
        *,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record an unconfirmable motion and latch further motion.

        The arm may still be moving when the gateway stops answering, so the
        adapter never guesses ``Completed``. Recovery requires an operator
        inspection and a backend restart; the latch is intentionally not
        cleared by a normal stop.
        """
        active = self._active
        command_id = active["command_id"] if active else self._last_command_id
        payload = dict(details or {})
        if self._motion_status_cache:
            payload.setdefault("last_gateway_status", self._motion_status_cache)
        self._events.append(
            self._event(
                CommandStage.UNKNOWN,
                command_id,
                reason,
                error_code or ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                payload,
            )
        )
        now = utc_now_iso()
        self._motion_latched = Fault(
            fault_id="motion-outcome-unknown",
            level=FaultLevel.FAULT,
            code=FaultCode.MOTION_OUTCOME_UNKNOWN.value,
            message=(
                "硬件运动结果无法确认；运动已锁定，"
                "检查并重启后端后才能继续"
            ),
            source="hardware_adapter",
            first_seen=now,
            last_seen=now,
            remediation=(
                "检查机械臂与厂商栈，然后重启后端；"
                "不要重试该命令"
            ),
            details={"command_id": command_id, "reason": reason},
        )
        self._active = None

    def _poll_motion(self, now: float) -> None:
        active = self._active
        if active is None or self._gateway is None:
            return
        if self._motion_status_at >= 0 and now - self._motion_status_at < 0.1:
            return
        try:
            status = self._gateway.get_json("/motion/status")
        except GatewayError as exc:
            self._finish_unknown(
                f"cannot confirm motion outcome: {exc}",
                error_code=ErrorCode.GATEWAY_UNAVAILABLE.value,
            )
            return
        self._motion_status_cache = status
        self._motion_status_at = now
        state = str(status.get("state") or "unknown")
        status_command = status.get("command_id")
        if status_command and status_command != active["command_id"]:
            self._finish_unknown(
                "gateway reports a different active command",
                details={"gateway_command_id": status_command},
            )
            return
        if state == "completed":
            cache = status.get("measured") or {}
            actual = status.get("actual") or {}
            verification = status.get("verification")
            timed = bool(active["targets"]) and all(
                target.get("completion") == "timed" for target in active["targets"]
            )
            if not timed and verification != "bus":
                # servo_states is the vendor command cache; without a bus read
                # this is not evidence that the arm reached the target.
                self._finish_unknown(
                    "gateway completed without a bus-servo verification",
                    details={
                        "verification": verification,
                        "reason": status.get("verification_reason"),
                        "commanded_cache": cache,
                        "actual": actual,
                    },
                )
                return
            deviations = {}
            if not timed:
                for target in active["targets"]:
                    item = actual.get(target["joint"]) or {}
                    raw = item.get("raw_position")
                    if raw is None:
                        continue
                    deviations[target["joint"]] = int(raw) - int(
                        target["target_pulse"]
                    )
            self._events.append(
                self._event(
                    CommandStage.OBSERVED,
                    active["command_id"],
                    (
                        "commanded interpolation time elapsed; bus register "
                        "recorded, not a force measurement"
                        if timed
                        else "bus-servo registers read after the interpolation"
                    ),
                    None,
                    {
                        "actual": actual,
                        "commanded_cache": cache,
                        "targets": active["targets"],
                        "verification": verification,
                    },
                )
            )
            self._events.append(
                self._event(
                    CommandStage.COMPLETED,
                    active["command_id"],
                    (
                        "gateway confirmed the timed gripper completion"
                        if timed
                        else "gateway confirmed actual bus registers within "
                        f"+/-{status.get('verification_tolerance_pulses')} pulses"
                    ),
                    None,
                    {
                        "actual": actual,
                        "commanded_cache": cache,
                        "deviations_pulses": deviations,
                        "duration_s": active["duration_s"],
                        "tolerance_pulses": status.get("tolerance_pulses"),
                        "verification_tolerance_pulses": status.get(
                            "verification_tolerance_pulses"
                        ),
                        "completion": "timed" if timed else "position",
                        "verification": verification,
                    },
                )
            )
            self._active = None
            self._last_command_id = active["command_id"]
            return
        if state == "stopped":
            measured = status.get("measured") or {}
            self._events.append(
                self._event(
                    CommandStage.OBSERVED,
                    active["command_id"],
                    "motion interrupted by a hold command; target not reached",
                    None,
                    {"measured": measured, "stopped": True},
                )
            )
            self._active = None
            self._last_command_id = active["command_id"]
            return
        if state in {"unknown", "rejected", "disabled"}:
            self._finish_unknown(
                f"gateway reports motion state {state!r}",
                details={"gateway": status},
            )
            return
        if now > active["deadline"]:
            self._finish_unknown(
                "motion did not confirm within duration + margin",
                details={
                    "duration_s": active["duration_s"],
                    "deadline_margin_s": active["deadline_margin_s"],
                    "gateway": status,
                },
            )

    def _gateway_joints(
        self, targets: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, DispatchResult | None]:
        """Convert normalized targets to the gateway's pulse payload."""
        joints: list[dict[str, Any]] = []
        for item in targets:
            name = str(item["joint"])
            if self.takeover is not None and name == self.takeover.gripper.joint:
                gripper = self.takeover.gripper
                target_rad = float(item["target_rad"])
                target_raw = int(item["target_raw"])
                expected_raw = gripper.rad_to_raw(target_rad)
                if abs(expected_raw - target_raw) > 1.0:
                    return None, DispatchResult(
                        CommandStage.FAILED,
                        ErrorCode.BAD_REQUEST.value,
                        f"{name}: target_raw {target_raw} disagrees with "
                        f"rad_to_raw({target_rad:.6f}) = {expected_raw:.3f}",
                    )
                if not (
                    gripper.min_rad - 1e-9
                    <= target_rad
                    <= gripper.max_rad + 1e-9
                ):
                    return None, DispatchResult(
                        CommandStage.FAILED,
                        ErrorCode.GRIPPER_RANGE.value,
                        f"{name}: {target_rad:.6f} rad outside the measured "
                        f"command range [{gripper.min_rad:.6f}, "
                        f"{gripper.max_rad:.6f}]",
                    )
                joints.append(
                    {
                        "joint": name,
                        "servo_id": gripper.servo_id,
                        "target_pulse": target_raw,
                        "target_rad": target_rad,
                        "completion": gripper.completion,
                    }
                )
                continue
            joint = self.mapping.joints.get(name)
            if joint is None:
                return None, DispatchResult(
                    CommandStage.FAILED,
                    ErrorCode.UNKNOWN_JOINT.value,
                    f"unknown joint: {name}",
                )
            target_rad = float(item["target_rad"])
            target_raw = int(item["target_raw"])
            expected_raw = joint.rad_to_raw(target_rad)
            if abs(expected_raw - target_raw) > 1.0:
                return None, DispatchResult(
                    CommandStage.FAILED,
                    ErrorCode.BAD_REQUEST.value,
                    f"{name}: target_raw {target_raw} disagrees with "
                    f"rad_to_raw({target_rad:.6f}) = {expected_raw:.3f}",
                )
            low, high = sorted((joint.min_raw, joint.max_raw))
            if not low <= target_raw <= high:
                return None, DispatchResult(
                    CommandStage.FAILED,
                    ErrorCode.OUT_OF_LIMIT.value,
                    f"{name}: pulse {target_raw} outside [{low}, {high}]",
                )
            joints.append(
                {
                    "joint": name,
                    "servo_id": joint.servo_id,
                    "target_pulse": target_raw,
                    "target_rad": target_rad,
                    "completion": "position",
                }
            )
        return joints, None

    def _dispatch_checks(self, targets: list[dict[str, Any]]) -> DispatchResult | None:
        if not self.motion_enabled:
            raise HardwareMotionDisabled(
                "Hardware motion is disabled; complete mapping verification and obtain Board approval"
            )
        if not self._motion_ready():
            raise HardwareMotionDisabled(
                "gateway does not advertise motion; start the supervised gateway instance"
            )
        if self._motion_latched is not None:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                "a previous motion outcome is unconfirmed; restart the backend after inspection",
            )
        if self._active is not None:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.ALREADY_EXECUTING.value,
                "a hardware motion is already executing",
            )
        if not targets:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.NO_TARGETS.value,
                "no targets to dispatch",
            )
        if self.motion_limits.single_joint_only and len(targets) != 1:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.SUPERVISED_SINGLE_JOINT.value,
                "supervised hardware motion accepts exactly one joint",
            )
        return None

    @_locked
    def dispatch(
        self,
        targets: list[dict[str, Any]],
        command_id: str,
        *,
        fast: bool = False,
    ) -> DispatchResult:
        blocked = self._dispatch_checks(targets)
        if blocked is not None:
            return blocked

        durations = {round(float(item["duration_s"]), 6) for item in targets}
        if len(durations) != 1:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.BAD_REQUEST.value,
                "hardware dispatch uses a single duration for every joint",
                {"durations_s": sorted(durations)},
            )
        duration = durations.pop()
        gripper_only = (
            len(targets) == 1
            and self.takeover is not None
            and str(targets[0]["joint"]) == self.takeover.gripper.joint
        )
        if (
            duration < self.motion_limits.min_duration_s - 1e-9
            and not gripper_only
        ):
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.DURATION_RANGE.value,
                f"duration {duration} s is below the motion floor "
                f"{self.motion_limits.min_duration_s} s",
            )

        joints, error = self._gateway_joints(targets)
        if error is not None:
            return error
        assert joints is not None

        payload = {
            "command_id": command_id,
            "client": "armpi-debug-studio-backend",
            "supervised": self.supervised,
            "mode": self.motion_scope,
            "duration_s": duration,
            "joints": joints,
        }
        if fast:
            payload["fast"] = True
        return self._post_dispatch(
            payload, command_id, joints, duration
        )

    @_locked
    def dispatch_profile(
        self,
        targets: list[dict[str, Any]],
        points: list[dict[str, Any]],
        command_id: str,
    ) -> DispatchResult:
        """Send one dense S-curve; the gateway re-validates every segment."""
        blocked = self._dispatch_checks(targets)
        if blocked is not None:
            return blocked
        if not points:
            return DispatchResult(
                CommandStage.FAILED,
                ErrorCode.NO_TARGETS.value,
                "no profile points to dispatch",
            )
        joints, error = self._gateway_joints(targets)
        if error is not None:
            return error
        assert joints is not None
        point_payload: list[dict[str, Any]] = []
        duration = 0.0
        for point in points:
            segment = float(point.get("duration_s") or 0.0)
            if segment <= 0.0:
                return DispatchResult(
                    CommandStage.FAILED,
                    ErrorCode.BAD_REQUEST.value,
                    "profile segment duration must be positive",
                )
            point_joints, point_error = self._gateway_joints(
                point.get("targets") or []
            )
            if point_error is not None:
                return point_error
            assert point_joints is not None
            duration += segment
            point_payload.append(
                {"duration_s": segment, "joints": point_joints}
            )
        payload = {
            "command_id": command_id,
            "client": "armpi-debug-studio-backend",
            "supervised": self.supervised,
            "mode": self.motion_scope,
            "duration_s": round(duration, 6),
            "joints": joints,
            "fast": True,
            "profile": {"points": point_payload},
        }
        return self._post_dispatch(payload, command_id, joints, duration)

    def _post_dispatch(
        self,
        payload: dict[str, Any],
        command_id: str,
        joints: list[dict[str, Any]],
        duration: float,
    ) -> DispatchResult:
        if self._gateway is None:  # pressed by the motion_enabled guard above
            raise HardwareMotionDisabled("no gateway client configured")
        try:
            response = self._gateway.post_json("/motion/dispatch", payload)
        except GatewayError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                return DispatchResult(
                    CommandStage.FAILED,
                    exc.code or ErrorCode.BAD_REQUEST.value,
                    str(exc),
                    {"gateway_status": exc.status, "gateway_code": exc.code},
                )
            # The POST may have reached the gateway before the connection
            # failed; never report Failed and never retry.
            return DispatchResult(
                CommandStage.UNKNOWN,
                exc.code or ErrorCode.GATEWAY_UNAVAILABLE.value,
                f"motion dispatch outcome is unknown: {exc}",
                {"gateway_status": exc.status, "gateway_code": exc.code},
            )

        now = self._clock()
        deadline_margin_s = (
            self.takeover.timeout_margin_s + 1.0
            if self.takeover is not None
            else 2.0
        )
        self._active = {
            "command_id": command_id,
            "targets": joints,
            "duration_s": duration,
            "started_at": now,
            "deadline": now + duration + deadline_margin_s,
            "deadline_margin_s": deadline_margin_s,
            "stop_requested": False,
        }
        self._last_command_id = command_id
        return DispatchResult(
            CommandStage.DISPATCHED,
            None,
            f"gateway published {len(joints)} joint target(s) for {duration} s",
            {"gateway": response},
        )

    @_locked
    def stop(self, command_id: str) -> StopResult:
        if not self.motion_enabled:
            return StopResult(
                state=StopState.STOP_UNKNOWN,
                command_id=command_id,
                detail="hardware gateway is read-only; stop is not implemented",
                evidence="no controller acknowledgement is available",
                requested_at=utc_now_iso(),
            )
        if self._gateway is None:
            return StopResult(
                state=StopState.STOP_UNKNOWN,
                command_id=command_id,
                detail="no gateway client configured",
                evidence="no request was sent",
                requested_at=utc_now_iso(),
            )
        try:
            response = self._gateway.post_json(
                "/motion/stop",
                {
                    "command_id": command_id,
                    "client": "armpi-debug-studio-backend",
                },
            )
        except GatewayError as exc:
            return StopResult(
                state=StopState.STOP_UNKNOWN,
                command_id=command_id,
                detail=f"hold command could not be confirmed: {exc}",
                evidence="no gateway acknowledgement",
                requested_at=utc_now_iso(),
            )
        if self._active is not None:
            self._active["stop_requested"] = True
        return StopResult(
            state=StopState.STOP_DISPATCHED,
            command_id=command_id,
            detail=(
                "hold command published at the last measured pulses; "
                "physical stop is not confirmed"
            ),
            evidence=str(response),
            requested_at=utc_now_iso(),
        )

    @_locked
    def drain_events(self) -> list[dict[str, Any]]:
        events, self._events = self._events, []
        return events

    @_locked
    def reset(self) -> None:
        # Never perform a hardware side effect from a mock reset endpoint.
        return None
